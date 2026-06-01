import hashlib

import dagster as dg

from orchestrators.config import EXTRACT_COMPLEX_CONTENTS_DAG_VERSION

from .def_config import (
    FETCHED_CONTENT_MIN_CHARS,
    PIPELINE_TAG,
    queue_items_partition_def,
)
from .resources import ExtractorRegistry, ExtractQueueStore, FetcherResource, NotionResource

GROUP_NAME = "extract_complex_contents"


@dg.asset(
    key=["extract_complex_contents", "routed_for_extraction"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Reads the queue_items row for this partition. Emits content_type "
        "metadata so per-type branch assets (youtube_*, arxiv_*) can decide "
        "whether to materialize work or yield a skipped MaterializeResult. "
        "Triage_queued_items must have populated content_type before this runs."
    ),
)
def routed_for_extraction(
    context: dg.AssetExecutionContext,
    store: ExtractQueueStore,
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
    url = row.get("url") or context.run.tags.get("url") or ""
    return dg.MaterializeResult(
        metadata={
            "notion_page_id": dg.MetadataValue.text(page_id),
            "content_type": dg.MetadataValue.text(content_type),
            "url": dg.MetadataValue.url(url) if url else dg.MetadataValue.text(""),
            "summary": dg.MetadataValue.md(f"**{content_type}** → routing to branch"),
        }
    )


@dg.asset(
    key=["extract_complex_contents", "youtube_transcript"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_complex_contents", "routed_for_extraction"])],
    description=(
        "Fetches the YouTube transcript via youtube-transcript-api. Skips "
        "when the partition's content_type is not YouTube — yields a "
        "MaterializeResult with skipped=True so the downstream branch asset "
        "(youtube_topic_card) also skips."
    ),
)
def youtube_transcript(
    context: dg.AssetExecutionContext,
    fetcher: FetcherResource,
    store: ExtractQueueStore,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    content_type = (row or {}).get("content_type")
    if content_type != "YouTube":
        return dg.MaterializeResult(
            metadata={
                "skipped": dg.MetadataValue.bool(True),
                "reason": dg.MetadataValue.text(f"content_type={content_type}"),
            }
        )

    if row and row.get("raw_content") and row.get("url"):
        url = row["url"]
        return dg.MaterializeResult(
            metadata={
                "url": dg.MetadataValue.url(url),
                "fetch_skipped": dg.MetadataValue.bool(True),
                "existing_content_chars": dg.MetadataValue.int(
                    row.get("fetched_content_char_count") or 0
                ),
                "summary": dg.MetadataValue.md(
                    "Skipped — raw_content already cached for this URL."
                ),
            }
        )

    url = (row or {}).get("url") or context.run.tags.get("url") or ""
    if not url:
        raise dg.Failure(description=f"Missing url for page_id={page_id}")

    result = fetcher.fetch_for_type(url, content_type="YouTube")
    if result.error:
        raise dg.Failure(
            description=f"YouTube fetch failed: {result.error}",
            metadata={
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )

    char_count = len(result.content)
    if char_count < FETCHED_CONTENT_MIN_CHARS:
        raise dg.Failure(
            description=f"YouTube transcript below floor: {char_count} chars",
            metadata={
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
    return dg.MaterializeResult(
        metadata={
            "url": dg.MetadataValue.url(url),
            "fetch_tier": dg.MetadataValue.text(result.tier),
            "content_chars": dg.MetadataValue.int(char_count),
            "title": dg.MetadataValue.text(result.title),
            "tier_log": dg.MetadataValue.json(result.tier_log),
            "content_hash_short": dg.MetadataValue.text(content_hash[:12]),
            "summary": dg.MetadataValue.md(f"**youtube** — {char_count:,} chars"),
        }
    )


@dg.asset(
    key=["extract_complex_contents", "youtube_topic_card"],
    group_name=GROUP_NAME,
    compute_kind="openai",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_complex_contents", "youtube_transcript"])],
    check_specs=[
        dg.AssetCheckSpec(
            name="topic_card_has_required_fields",
            asset=dg.AssetKey(["extract_complex_contents", "youtube_topic_card"]),
            blocking=True,
            description=(
                "extracted_title set AND at least one of core_mechanism / "
                "best_example populated."
            ),
        ),
    ],
    description=(
        "Extracts Topic Card from YouTube transcript via the v5_youtube prompt. "
        "Skips when content_type is not YouTube."
    ),
)
def youtube_topic_card(
    context: dg.AssetExecutionContext,
    extractor: ExtractorRegistry,
    store: ExtractQueueStore,
):
    page_id = context.partition_key
    row = store.get_row(page_id)
    if (row or {}).get("content_type") != "YouTube":
        yield dg.MaterializeResult(
            metadata={
                "skipped": dg.MetadataValue.bool(True),
                "reason": dg.MetadataValue.text(f"content_type={(row or {}).get('content_type')}"),
            }
        )
        yield dg.AssetCheckResult(
            check_name="topic_card_has_required_fields",
            passed=True,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"skipped": dg.MetadataValue.bool(True)},
        )
        return

    if not row or not row.get("raw_content"):
        raise dg.Failure(
            description=(
                f"No raw_content for page_id={page_id}; youtube_transcript must produce one."
            ),
        )

    extraction, usage = extractor.extract(content=row["raw_content"], content_type="YouTube")
    store.update_extracted(
        notion_page_id=page_id,
        extraction=extraction,
        prompt_label=extractor.prompt_label("YouTube"),
        prompt_sha256=extractor.prompt_sha256("YouTube"),
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
            "extraction_prompt_label": dg.MetadataValue.text(extractor.prompt_label("YouTube")),
            "extraction_model": dg.MetadataValue.text(extractor.model),
            "prompt_sha256_short": dg.MetadataValue.text(extractor.prompt_sha256("YouTube")[:12]),
            "tokens_in": dg.MetadataValue.int(usage.input_tokens),
            "tokens_out": dg.MetadataValue.int(usage.output_tokens),
            "extracted_title": dg.MetadataValue.text(extraction.get("extracted_title") or ""),
            "field_count": dg.MetadataValue.int(field_count),
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
    key=["extract_complex_contents", "arxiv_pdf_text"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_complex_contents", "routed_for_extraction"])],
    description=(
        "Fetches the arXiv PDF text. Skips when the partition's content_type "
        "is not arXiv — yields a MaterializeResult with skipped=True so the "
        "downstream branch asset (arxiv_topic_card) also skips."
    ),
)
def arxiv_pdf_text(
    context: dg.AssetExecutionContext,
    fetcher: FetcherResource,
    store: ExtractQueueStore,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    content_type = (row or {}).get("content_type")
    if content_type != "arXiv":
        return dg.MaterializeResult(
            metadata={
                "skipped": dg.MetadataValue.bool(True),
                "reason": dg.MetadataValue.text(f"content_type={content_type}"),
            }
        )

    if row and row.get("raw_content") and row.get("url"):
        url = row["url"]
        return dg.MaterializeResult(
            metadata={
                "url": dg.MetadataValue.url(url),
                "fetch_skipped": dg.MetadataValue.bool(True),
                "existing_content_chars": dg.MetadataValue.int(
                    row.get("fetched_content_char_count") or 0
                ),
                "summary": dg.MetadataValue.md(
                    "Skipped — raw_content already cached for this URL."
                ),
            }
        )

    url = (row or {}).get("url") or context.run.tags.get("url") or ""
    if not url:
        raise dg.Failure(description=f"Missing url for page_id={page_id}")

    result = fetcher.fetch_for_type(url, content_type="arXiv")
    if result.error:
        raise dg.Failure(
            description=f"arXiv fetch failed: {result.error}",
            metadata={
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )

    char_count = len(result.content)
    if char_count < FETCHED_CONTENT_MIN_CHARS:
        raise dg.Failure(
            description=f"arXiv PDF text below floor: {char_count} chars",
            metadata={
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
        "url": dg.MetadataValue.url(url),
        "fetch_tier": dg.MetadataValue.text(result.tier),
        "content_chars": dg.MetadataValue.int(char_count),
        "tier_log": dg.MetadataValue.json(result.tier_log),
        "content_hash_short": dg.MetadataValue.text(content_hash[:12]),
        "summary": dg.MetadataValue.md(f"**arxiv** — {char_count:,} chars"),
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
    key=["extract_complex_contents", "arxiv_topic_card"],
    group_name=GROUP_NAME,
    compute_kind="openai",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["extract_complex_contents", "arxiv_pdf_text"])],
    check_specs=[
        dg.AssetCheckSpec(
            name="topic_card_has_required_fields",
            asset=dg.AssetKey(["extract_complex_contents", "arxiv_topic_card"]),
            blocking=True,
            description=(
                "extracted_title set AND at least one of core_mechanism / "
                "best_example populated."
            ),
        ),
    ],
    description=(
        "Extracts Topic Card from arXiv PDF text via the v5_arxiv prompt. "
        "Skips when content_type is not arXiv."
    ),
)
def arxiv_topic_card(
    context: dg.AssetExecutionContext,
    extractor: ExtractorRegistry,
    store: ExtractQueueStore,
):
    page_id = context.partition_key
    row = store.get_row(page_id)
    if (row or {}).get("content_type") != "arXiv":
        yield dg.MaterializeResult(
            metadata={
                "skipped": dg.MetadataValue.bool(True),
                "reason": dg.MetadataValue.text(f"content_type={(row or {}).get('content_type')}"),
            }
        )
        yield dg.AssetCheckResult(
            check_name="topic_card_has_required_fields",
            passed=True,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"skipped": dg.MetadataValue.bool(True)},
        )
        return

    if not row or not row.get("raw_content"):
        raise dg.Failure(
            description=f"No raw_content for page_id={page_id}; arxiv_pdf_text must produce one.",
        )

    extraction, usage = extractor.extract(content=row["raw_content"], content_type="arXiv")
    store.update_extracted(
        notion_page_id=page_id,
        extraction=extraction,
        prompt_label=extractor.prompt_label("arXiv"),
        prompt_sha256=extractor.prompt_sha256("arXiv"),
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
            "extraction_prompt_label": dg.MetadataValue.text(extractor.prompt_label("arXiv")),
            "extraction_model": dg.MetadataValue.text(extractor.model),
            "prompt_sha256_short": dg.MetadataValue.text(extractor.prompt_sha256("arXiv")[:12]),
            "tokens_in": dg.MetadataValue.int(usage.input_tokens),
            "tokens_out": dg.MetadataValue.int(usage.output_tokens),
            "extracted_title": dg.MetadataValue.text(extraction.get("extracted_title") or ""),
            "field_count": dg.MetadataValue.int(field_count),
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
    key=["extract_complex_contents", "persisted"],
    group_name=GROUP_NAME,
    compute_kind="notion",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[
        dg.AssetDep(["extract_complex_contents", "youtube_topic_card"]),
        dg.AssetDep(["extract_complex_contents", "arxiv_topic_card"]),
    ],
    description=(
        "Convergent sink. Verifies extraction completed for this partition "
        "(queue_items.extracted_at non-NULL) and flips Notion Status=Ready. "
        "Always runs after the per-type topic_card assets — exactly one of "
        "them produced a real extraction; the other skipped."
    ),
)
def persisted(
    context: dg.AssetExecutionContext,
    notion: NotionResource,
    store: ExtractQueueStore,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    if not row or not row.get("extracted_at"):
        raise dg.Failure(
            description=(
                f"No extraction completed for page_id={page_id} — both per-type "
                "branches skipped or failed. Sensor filter should prevent this; "
                "if you see it, investigate the upstream topic_card assets."
            ),
            metadata={
                "content_type": dg.MetadataValue.text((row or {}).get("content_type") or "(none)"),
            },
        )
    notion.update_status(page_id, "Ready")
    return dg.MaterializeResult(
        metadata={
            "notion_page_id": dg.MetadataValue.text(page_id),
            "content_type": dg.MetadataValue.text(row.get("content_type") or "(none)"),
            "extracted_at": dg.MetadataValue.text(row.get("extracted_at") or ""),
            "summary": dg.MetadataValue.md("Notion → Ready"),
        }
    )


all_assets = [
    routed_for_extraction,
    youtube_transcript,
    youtube_topic_card,
    arxiv_pdf_text,
    arxiv_topic_card,
    persisted,
]
