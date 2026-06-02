import hashlib
import json
import textwrap

import dagster as dg

from orchestrators.config import EXTRACT_COMPLEX_CONTENTS_DAG_VERSION
from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

from .def_config import (
    FETCHED_CONTENT_MIN_CHARS,
    PIPELINE_TAG,
    queue_items_partition_def,
)
from .resources import ExtractorRegistry, FetcherResource

GROUP_NAME = "extract_complex_contents"

_PREVIEW_HEAD = 500
_PREVIEW_TAIL = 500


def _oneline(s: str) -> str:
    """Collapse a multi-line source string into a single-paragraph string.

    Lets us write Dagster `description=` blocks as readable multi-line source
    while the rendered string in the Dagster UI stays a single paragraph."""
    return " ".join(textwrap.dedent(s).split())


def _preview(content: str, *, head: int = _PREVIEW_HEAD, tail: int = _PREVIEW_TAIL) -> str:
    """Head + tail preview with an ellipsis marker for the middle.

    Returns the full content if shorter than head + tail. Otherwise returns
    a string of shape `{head}\n\n... [N chars omitted] ...\n\n{tail}` so the
    UI shows the start and end without flooding the page on multi-KB content."""
    if len(content) <= head + tail:
        return content
    omitted = len(content) - head - tail
    return f"{content[:head]}\n\n... [{omitted:,} chars omitted] ...\n\n{content[-tail:]}"


@dg.asset(
    key=["extract_complex_contents", "fetched"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=_oneline(
        """
        Dispatches to the per-type fetcher (YouTube transcript / arXiv PDF
        text / article cascade) via FetcherResource, validates the content
        is above the floor, and persists raw_content to queue_items. Cache:
        skips the network fetch if raw_content is already set for this row.
        """
    ),
)
def fetched(
    context: dg.AssetExecutionContext,
    fetcher: FetcherResource,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    store.ensure_schema()
    row = store.get_row(page_id)
    if not row:
        raise dg.Failure(
            description=f"No queue_items row for partition {page_id}; triage must run first.",
        )
    content_type = row.get("content_type")
    if not content_type:
        raise dg.Failure(
            description=f"queue_items row for {page_id} has no content_type; triage incomplete.",
        )

    if row.get("raw_content") and row.get("url"):
        url = row["url"]
        return dg.MaterializeResult(
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_skipped": dg.MetadataValue.bool(True),
                "existing_content_chars": dg.MetadataValue.int(
                    row.get("fetched_content_char_count") or 0
                ),
                "content_preview": dg.MetadataValue.md(f"```\n{_preview(row['raw_content'])}\n```"),
                "summary": dg.MetadataValue.md(
                    "Skipped — raw_content already cached for this URL."
                ),
            }
        )

    url = row.get("url") or context.run.tags.get("url") or ""
    if not url:
        raise dg.Failure(description=f"Missing url for page_id={page_id}")

    result = fetcher.fetch_for_type(url, content_type=content_type)
    if result.error:
        raise dg.Failure(
            description=f"{content_type} fetch failed: {result.error}",
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )

    char_count = len(result.content)
    if char_count < FETCHED_CONTENT_MIN_CHARS:
        raise dg.Failure(
            description=f"{content_type} content below floor: {char_count} chars",
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "content_chars": dg.MetadataValue.int(char_count),
                "min_chars": dg.MetadataValue.int(FETCHED_CONTENT_MIN_CHARS),
                "tier_log": dg.MetadataValue.json(result.tier_log),
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

    extras = result.extras or {}
    metadata: dict[str, dg.MetadataValue] = {
        "content_type": dg.MetadataValue.text(content_type),
        "url": dg.MetadataValue.url(url),
        "fetch_tier": dg.MetadataValue.text(result.tier),
        "content_chars": dg.MetadataValue.int(char_count),
        "tier_log": dg.MetadataValue.json(result.tier_log),
        "content_hash_short": dg.MetadataValue.text(content_hash[:12]),
        "content_preview": dg.MetadataValue.md(f"```\n{_preview(result.content)}\n```"),
        "summary": dg.MetadataValue.md(f"**{content_type}** — {char_count:,} chars"),
    }
    if result.title:
        metadata["title"] = dg.MetadataValue.text(result.title)
    if extras.get("authors"):
        metadata["authors"] = dg.MetadataValue.text(str(extras["authors"]))
    if extras.get("published"):
        metadata["published"] = dg.MetadataValue.text(str(extras["published"]))
    if extras.get("arxiv_id"):
        metadata["arxiv_id"] = dg.MetadataValue.text(str(extras["arxiv_id"]))
    return dg.MaterializeResult(metadata=metadata)


@dg.asset(
    key=["extract_complex_contents", "extracted"],
    group_name=GROUP_NAME,
    compute_kind="openai",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_complex_contents", "fetched"])],
    check_specs=[
        dg.AssetCheckSpec(
            name="topic_card_has_required_fields",
            asset=dg.AssetKey(["extract_complex_contents", "extracted"]),
            blocking=True,
            description=(
                "extracted_title set AND at least one of core_mechanism / "
                "best_example populated."
            ),
        ),
    ],
    description=_oneline(
        """
        Dispatches to the per-type extractor strategy via ExtractorRegistry
        (v1: SingleShotOpenAIExtractor for every type, per-type prompt).
        Persists the Topic Card fields + provenance to queue_items. The
        registry pattern lets future per-type swaps (e.g. LangGraph for
        arXiv) land without asset edits.
        """
    ),
)
def extracted(
    context: dg.AssetExecutionContext,
    extractor: ExtractorRegistry,
    store: QueueStoreResource,
):
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("raw_content"):
        raise dg.Failure(
            description=f"No raw_content for page_id={page_id}; fetched must produce one.",
        )
    content_type = row["content_type"]

    extraction, usage = extractor.extract(content=row["raw_content"], content_type=content_type)
    store.update_extracted(
        notion_page_id=page_id,
        extraction=extraction,
        prompt_label=extractor.prompt_label(content_type),
        prompt_sha256=extractor.prompt_sha256(content_type),
        model=extractor.model,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
    )

    field_count = sum(1 for v in extraction.values() if v)
    passed = bool(extraction.get("extracted_title")) and bool(
        extraction.get("core_mechanism") or extraction.get("best_example")
    )
    extraction_json = json.dumps(extraction, indent=2, ensure_ascii=False)

    yield dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(content_type),
            "extraction_prompt_label": dg.MetadataValue.text(extractor.prompt_label(content_type)),
            "extraction_model": dg.MetadataValue.text(extractor.model),
            "prompt_sha256_short": dg.MetadataValue.text(
                extractor.prompt_sha256(content_type)[:12]
            ),
            "tokens_in": dg.MetadataValue.int(usage.input_tokens),
            "tokens_out": dg.MetadataValue.int(usage.output_tokens),
            "extracted_title": dg.MetadataValue.text(extraction.get("extracted_title") or ""),
            "field_count": dg.MetadataValue.int(field_count),
            "extraction_preview": dg.MetadataValue.md(f"```json\n{_preview(extraction_json)}\n```"),
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
    key=["extract_complex_contents", "published"],
    group_name=GROUP_NAME,
    compute_kind="notion",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_complex_contents", "extracted"])],
    description=_oneline(
        """
        Flips Notion Status=Ready and overwrites Description with the
        extracted core_mechanism (sharper than the triage-seeded HTML meta).
        Isolated from extraction so a Notion API hiccup can be retried
        without re-spending an OpenAI extraction. Verifies
        queue_items.extracted_at is set as a guard against bad ordering.
        """
    ),
)
def published(
    context: dg.AssetExecutionContext,
    notion: NotionQueueResource,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("extracted_at"):
        raise dg.Failure(
            description=(
                f"No extraction completed for page_id={page_id} — extracted " "must run first."
            ),
            metadata={
                "content_type": dg.MetadataValue.text((row or {}).get("content_type") or "(none)"),
            },
        )
    extraction = json.loads(row.get("extraction_payload") or "{}")
    core_mechanism = extraction.get("core_mechanism")
    notion.update_status(page_id, "Ready", description=core_mechanism)
    return dg.MaterializeResult(
        metadata={
            "notion_page_id": dg.MetadataValue.text(page_id),
            "content_type": dg.MetadataValue.text(row.get("content_type") or "(none)"),
            "extracted_at": dg.MetadataValue.text(row.get("extracted_at") or ""),
            "core_mechanism": dg.MetadataValue.text(core_mechanism or ""),
            "summary": dg.MetadataValue.md("Notion → Ready"),
        }
    )


all_assets = [fetched, extracted, published]
