import hashlib

import dagster as dg

from orchestrators.config import EXTRACT_QUEUED_ITEMS_DAG_VERSION

from .def_config import (
    FETCHED_CONTENT_MIN_CHARS,
    PIPELINE_TAG,
    queue_items_partition_def,
)
from .resources import ExtractorResource, ExtractQueueStore, FetcherResource, NotionResource

GROUP_NAME = "extract_queued_items"


@dg.asset(
    key=["extract_queued_items", "fetched_content"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=EXTRACT_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Fetches the URL from a Notion queue row. Jina first; falls through to "
        "curl-cffi via Pi SOCKS5 (safari17_0 impersonation) when Jina returns "
        "less than FETCHED_CONTENT_MIN_CHARS. Writes raw_content directly to "
        "queue_items in SQLite. Skips re-fetch when raw_content is already "
        "cached for the same URL."
    ),
)
def fetched_content(
    context: dg.AssetExecutionContext,
    fetcher: FetcherResource,
    notion: NotionResource,
    store: ExtractQueueStore,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    url = context.run.tags.get("url")
    if not url:
        raise dg.Failure(description=f"Missing 'url' run tag for page_id={page_id}")

    store.ensure_schema()
    existing = store.get_row(page_id)
    if existing and existing.get("raw_content") and existing.get("url") == url:
        return dg.MaterializeResult(
            metadata={
                "url": dg.MetadataValue.url(url),
                "fetch_skipped": dg.MetadataValue.bool(True),
                "existing_fetch_tier": dg.MetadataValue.text(existing.get("fetch_tier") or "?"),
                "existing_content_chars": dg.MetadataValue.int(
                    existing.get("fetched_content_char_count") or 0
                ),
                "summary": dg.MetadataValue.md(
                    "Skipped — raw_content already cached for this URL."
                ),
            }
        )

    notion.update_status(page_id, "Fetching")
    result = fetcher.fetch(url)
    char_count = len(result.content)

    if char_count < FETCHED_CONTENT_MIN_CHARS:
        raise dg.Failure(
            description=f"Fetched content below floor: {char_count} chars",
            metadata={
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "content_chars": dg.MetadataValue.int(char_count),
                "min_chars": dg.MetadataValue.int(FETCHED_CONTENT_MIN_CHARS),
                "fetch_tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )

    content_hash = hashlib.sha256(result.content.encode()).hexdigest()
    store.upsert_fetched(
        notion_page_id=page_id,
        url=url,
        raw_content=result.content,
        fetch_tier=result.tier,
        fetch_tier_log=result.tier_log,
        fetched_content_char_count=char_count,
        content_hash=content_hash,
    )
    return dg.MaterializeResult(
        metadata={
            "url": dg.MetadataValue.url(url),
            "fetch_tier": dg.MetadataValue.text(result.tier),
            "content_chars": dg.MetadataValue.int(char_count),
            "fetch_tier_log": dg.MetadataValue.json(result.tier_log),
            "content_hash_short": dg.MetadataValue.text(content_hash[:12]),
            "summary": dg.MetadataValue.md(f"**{result.tier}** — {char_count:,} chars"),
        }
    )


@dg.asset(
    key=["extract_queued_items", "topic_card"],
    group_name=GROUP_NAME,
    compute_kind="anthropic",
    code_version=EXTRACT_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_queued_items", "fetched_content"])],
    check_specs=[
        dg.AssetCheckSpec(
            name="topic_card_has_required_fields",
            asset=dg.AssetKey(["extract_queued_items", "topic_card"]),
            blocking=True,
            description=(
                "extracted_title set AND at least one of "
                "core_mechanism / best_example populated."
            ),
        ),
    ],
    description=(
        "Extracts the Topic Card via Anthropic. Reads raw_content from the "
        "queue_items row written by fetched_content. UPDATE-on-re-extract: a "
        "fresh prompt label overwrites the prior extraction in place."
    ),
)
def topic_card(
    context: dg.AssetExecutionContext,
    extractor: ExtractorResource,
    store: ExtractQueueStore,
):
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("raw_content"):
        raise dg.Failure(
            description=f"No raw_content for page_id={page_id}; fetched_content must run first.",
        )

    extraction, usage = extractor.extract(content=row["raw_content"])
    store.update_extracted(
        notion_page_id=page_id,
        extraction=extraction,
        prompt_label=extractor.prompt_label,
        prompt_sha256=extractor.prompt_sha256,
        model=extractor.model,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
    )

    field_count = sum(1 for v in extraction.values() if v)
    passed = bool(extraction.get("extracted_title")) and bool(
        extraction.get("core_mechanism") or extraction.get("best_example")
    )

    yield dg.MaterializeResult(
        metadata={
            "extraction_prompt_label": dg.MetadataValue.text(extractor.prompt_label),
            "extraction_model": dg.MetadataValue.text(extractor.model),
            "prompt_sha256_short": dg.MetadataValue.text(extractor.prompt_sha256[:12]),
            "tokens_in": dg.MetadataValue.int(usage.input_tokens),
            "tokens_out": dg.MetadataValue.int(usage.output_tokens),
            "extracted_title": dg.MetadataValue.text(extraction.get("extracted_title") or ""),
            "field_count": dg.MetadataValue.int(field_count),
            "candidate_tie_backs": dg.MetadataValue.json(
                extraction.get("candidate_tie_backs") or []
            ),
            "summary": dg.MetadataValue.md(
                f"**{extraction.get('extracted_title') or '(no title)'}** — "
                f"{field_count}/7 fields populated"
            ),
        }
    )
    yield dg.AssetCheckResult(
        check_name="topic_card_has_required_fields",
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "extracted_title": dg.MetadataValue.text(extraction.get("extracted_title") or ""),
            "field_count": dg.MetadataValue.int(field_count),
        },
    )


@dg.asset(
    key=["extract_queued_items", "persisted"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=EXTRACT_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_queued_items", "topic_card"])],
    description=(
        "Flips the Notion lifecycle row to Ready. Topic Card is already in the "
        "local store; this asset is the lifecycle-write boundary so a Notion "
        "outage doesn't lose extraction work."
    ),
)
def persisted(context: dg.AssetExecutionContext, notion: NotionResource) -> dg.MaterializeResult:
    page_id = context.partition_key
    notion.update_status(page_id, "Ready")
    return dg.MaterializeResult(
        metadata={
            "notion_page_id": dg.MetadataValue.text(page_id),
            "summary": dg.MetadataValue.md("Notion → Ready"),
        }
    )


all_assets = [fetched_content, topic_card, persisted]
