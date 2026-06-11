import hashlib
import textwrap
from typing import Any

import dagster as dg

from orchestrators.config import EXTRACT_COMPLEX_CONTENTS_DAG_VERSION
from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

from .def_config import (
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


def _format_tier_log(tier_log: list[dict[str, Any]]) -> str:
    """Render the fetcher's tier_log as a multi-line headline for dg.Failure.

    The raw list is still stored in metadata; this version is what shows up
    in the description so you don't have to expand the event to learn why
    each tier failed. Each entry is one line, e.g.
        jina:      HTTP 401, 0 chars, 312ms — jina HTTP 401: <body…>
        curl_cffi: HTTP 200, 1187 chars (below 1500 floor), 2.1s
        tavily:    status 0, 0 chars (empty), 850ms — tavily returned …
    """
    if not tier_log:
        return "  (no tier log)"
    lines: list[str] = []
    name_width = max((len(str(e.get("tier") or "?")) for e in tier_log), default=0)
    for entry in tier_log:
        tier = str(entry.get("tier") or "?").ljust(name_width)
        status = entry.get("status")
        chars = entry.get("chars", 0)
        floor = entry.get("floor")
        kind = entry.get("error_kind") or entry.get("error") or ""
        duration_ms = entry.get("duration_ms") or 0
        detail = entry.get("detail")

        status_part = f"HTTP {status}" if status else "status 0"
        chars_part = f"{chars} chars"
        if kind == "below_floor" and floor:
            chars_part = f"{chars} chars (below {floor} floor)"
        elif kind == "validation_failed":
            chars_part = f"{chars} chars (validation failed)"
        elif kind == "empty" or chars == 0:
            chars_part = "0 chars (empty)"
        duration_part = f"{duration_ms}ms" if duration_ms < 1000 else f"{duration_ms / 1000:.1f}s"
        head = f"  {tier}: {status_part}, {chars_part}, {duration_part}"
        if detail:
            head += f" — {detail}"
        lines.append(head)
    return "\n".join(lines)


@dg.asset(
    key=["extract_complex_contents", "fetched"],
    group_name=GROUP_NAME,
    compute_kind="http",
    code_version=EXTRACT_COMPLEX_CONTENTS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    retry_policy=dg.RetryPolicy(max_retries=1, delay=120),
    description=_oneline(
        """
        POSTs to the fetcher service's /v1/fetch and persists the returned
        markdown + tier_log to queue_items. Cache: skips when raw_content
        is already set.
        """
    ),
)
def fetched(
    context: dg.AssetExecutionContext,
    fetcher: FetcherResource,
    store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
    store.ensure_schema()
    row = store.get_row(page_id)
    if not row:
        raise dg.Failure(
            description=(
                f"No local queue_items row for Notion page {notion_url}. "
                f"Triage did not seed this page — likely a manual Status flip, "
                f"a wrong NOTION_QUEUE_DB_ID, or a queue.db restored before this row."
            ),
            allow_retries=False,
            metadata={
                "notion_url": dg.MetadataValue.url(notion_url),
                "notion_page_id": dg.MetadataValue.text(page_id),
            },
        )
    content_type = row.get("content_type")
    if not content_type:
        raise dg.Failure(
            description=(
                f"queue_items row for {notion_url} has no Content Type. "
                f"Set one in Notion, or flip Status back to Queued so triage reclassifies."
            ),
            allow_retries=False,
            metadata={
                "notion_url": dg.MetadataValue.url(notion_url),
                "notion_page_id": dg.MetadataValue.text(page_id),
            },
        )

    if row.get("raw_content") and row.get("url"):
        url = row["url"]
        canonical = row.get("canonical_url") or ""
        return dg.MaterializeResult(
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "canonical_url": dg.MetadataValue.text(canonical),
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
        raise dg.Failure(description=f"Missing url for page_id={page_id}", allow_retries=False)

    override = row.get("raw_content_override") or ""
    if override:
        result = fetcher.structure(override, source_url=url)
    else:
        result = fetcher.fetch_for_type(url, content_type=content_type)
    if result.error:
        raise dg.Failure(
            description=(
                f"{content_type} fetch failed for {url}: {result.error}\n"
                f"{_format_tier_log(result.tier_log)}"
            ),
            allow_retries=result.transient,
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "tier_log": dg.MetadataValue.json(result.tier_log),
            },
        )

    char_count = len(result.content)
    # The fetcher cascade falls back to `best_result` when no tier hits its
    # floor (services/fetcher/cascade.py), so a 200 can carry sub-floor
    # content. Guard the extractor against degenerate inputs before persist.
    if char_count < 500:
        raise dg.Failure(
            description=(
                f"{content_type} fetch below extraction floor for {url}: "
                f"{char_count} chars from tier '{result.tier}'\n"
                f"{_format_tier_log(result.tier_log)}"
            ),
            allow_retries=False,
            metadata={
                "content_type": dg.MetadataValue.text(content_type),
                "url": dg.MetadataValue.url(url),
                "fetch_tier": dg.MetadataValue.text(result.tier),
                "content_chars": dg.MetadataValue.int(char_count),
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
        "canonical_url": dg.MetadataValue.text(row.get("canonical_url") or ""),
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
                "Topic Card has extracted_title + core_mechanism, AND Followups "
                "has at least 4 questions."
            ),
        ),
    ],
    description=_oneline(
        """
        Runs the three-call extractor via ExtractorRegistry (v2:
        ThreeCallOpenAIExtractor — narrative + topic_card + followups in
        one Dagster op, calls 2+3 in parallel via asyncio.gather). Persists
        one row per call into extraction_calls + updates queue_items cohort
        fields atomically. Future LangGraph swap is a one-class change
        inside the registry; no asset edits required.
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

    # Build the extractor ONCE per materialization. It owns an AsyncOpenAI
    # client closed at the end of `.extract()`; calling `build()` again
    # would leak a fresh httpx pool. Reads of `bundle_label` /
    # `bundle_sha256` / `model` below are property accesses on this
    # instance — no extra client construction.
    ex = extractor.build()
    payload, calls = ex.extract(content=row["raw_content"], content_type=content_type)

    tokens_in_total = sum(c.tokens_in for c in calls)
    tokens_out_total = sum(c.tokens_out for c in calls)
    by_kind = {c.call_kind: c for c in calls}

    # Two perspectives on the cohort time:
    #   - total_model_time_ms: sum of per-call durations (what you pay for)
    #   - wall_clock_ms: narrative ran first (sequential), then topic_card
    #     and followups in parallel inside asyncio.gather — so user-visible
    #     latency is narrative + max(topic_card, followups), not the sum.
    durations = {k: (v.duration_ms or 0) for k, v in by_kind.items()}
    total_model_time_ms = int(sum(durations.values()))
    wall_clock_ms = int(
        durations.get("narrative", 0)
        + max(durations.get("topic_card", 0), durations.get("followups", 0))
    )

    store.record_extraction_calls(
        notion_page_id=page_id,
        extractor_label=ex.bundle_label,
        extractor_sha256=ex.bundle_sha256,
        model=ex.model,
        calls=calls,
        tokens_in_total=tokens_in_total,
        tokens_out_total=tokens_out_total,
    )
    # Fold -wal sidecar into the main queue.db, allowing readers to read all rows
    store.checkpoint_wal()

    topic_card = payload.topic_card
    followups = payload.followups
    passed = (
        bool(topic_card.extracted_title)
        and bool(topic_card.core_mechanism)
        and len(followups.questions) >= 4
    )

    yield dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(content_type),
            "extractor_label": dg.MetadataValue.text(ex.bundle_label),
            "extractor_sha256_short": dg.MetadataValue.text(ex.bundle_sha256[:12]),
            "extraction_model": dg.MetadataValue.text(ex.model),
            "extracted_title": dg.MetadataValue.text(topic_card.extracted_title),
            "narrative_chars": dg.MetadataValue.int(len(payload.narrative_md)),
            "followups_count": dg.MetadataValue.int(len(followups.questions)),
            "tokens_in_total": dg.MetadataValue.int(tokens_in_total),
            "tokens_out_total": dg.MetadataValue.int(tokens_out_total),
            "total_model_time_ms": dg.MetadataValue.int(total_model_time_ms),
            "wall_clock_ms": dg.MetadataValue.int(wall_clock_ms),
            "prompt_sha_short_narrative": dg.MetadataValue.text(
                by_kind["narrative"].prompt_sha256[:12]
            ),
            "prompt_sha_short_topic_card": dg.MetadataValue.text(
                by_kind["topic_card"].prompt_sha256[:12]
            ),
            "prompt_sha_short_followups": dg.MetadataValue.text(
                by_kind["followups"].prompt_sha256[:12]
            ),
            "narrative_preview": dg.MetadataValue.md(f"```\n{_preview(payload.narrative_md)}\n```"),
            "topic_card_preview": dg.MetadataValue.md(
                f"```json\n{_preview(topic_card.model_dump_json(indent=2))}\n```"
            ),
            "summary": dg.MetadataValue.md(
                f"**{topic_card.extracted_title}** — 3-call extraction, "
                f"{len(followups.questions)} chips, {wall_clock_ms}ms wall / "
                f"{total_model_time_ms}ms model"
            ),
        }
    )
    yield dg.AssetCheckResult(
        check_name="topic_card_has_required_fields",
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "extracted_title": dg.MetadataValue.text(topic_card.extracted_title),
            "followups_count": dg.MetadataValue.int(len(followups.questions)),
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
    topic_card = store.get_latest_topic_card(page_id)
    core_mechanism = topic_card.core_mechanism if topic_card else None
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
