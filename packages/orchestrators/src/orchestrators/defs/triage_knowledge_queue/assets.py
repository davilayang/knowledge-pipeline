import json
import re
import textwrap
import time

import dagster as dg

from orchestrators.config import TRIAGE_KNOWLEDGE_QUEUE_DAG_VERSION
from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)

from .classify import (
    ALL_CONTENT_TYPES,
    CONTENT_TYPE_ARXIV,
    CONTENT_TYPE_PODCAST,
    CONTENT_TYPE_YOUTUBE,
    classify_content_type,
    normalize_url,
)
from .content_shape import (
    ALL_CONTENT_SHAPES,
    SHAPE_PODCAST_EPISODE,
    SHAPE_RESEARCH_SUMMARY,
    SHAPE_UNKNOWN,
)
from .content_shape_llm import ContentShapeClassifier
from .def_config import PIPELINE_TAG, queue_items_partition_def
from .display import resolve_display_description, resolve_display_title
from .enrich import EnrichmentSignals, enrich_url
from .podcast_canonicalize import maybe_redirect_podcast_to_youtube
from .url_meta import fetch_url_meta

GROUP_NAME = "triage_knowledge_queue"


# Notion auto-assigns a default title to fresh rows: "Untitled", or for
# database rows the locale-specific "New <db_name> page" pattern (e.g.
# "New queued page" for a "Queue" database). Treat these as blank so
# triage seeds the Name from the fetched page title — without this guard,
# the auto-default counts as a user-set title and triage refuses to
# overwrite it.
_NOTION_AUTO_NAMES = {"untitled"}
_NOTION_NEW_PAGE_RE = re.compile(r"^new\s+\S.*\s+page$", re.IGNORECASE)


def _is_user_set_name(name: str | None) -> bool:
    if not name:
        return False
    stripped = name.strip()
    if not stripped:
        return False
    if stripped.lower() in _NOTION_AUTO_NAMES:
        return False
    if _NOTION_NEW_PAGE_RE.match(stripped):
        return False
    return True


def _oneline(s: str) -> str:
    """Collapse a multi-line source string into a single-paragraph string.

    Lets us write Dagster `description=` blocks as readable multi-line source
    while the rendered string in the Dagster UI stays a single paragraph."""
    return " ".join(textwrap.dedent(s).split())


class EnrichedInput(dg.Config):
    """Typed input for the `enriched` asset. Sensor populates from Notion's
    URL property; manual UI launches must supply via the Launchpad. Same
    `url` as `TriageInput` — kept as a separate Config so each asset's
    Launchpad form is minimal and each op's contract is explicit."""

    url: str


@dg.asset(
    key=["triage_knowledge_queue", "enriched"],
    group_name=GROUP_NAME,
    kinds={"http", "sqlite"},
    code_version=TRIAGE_KNOWLEDGE_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    deps=[dg.AssetDep("notion_queue")],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=_oneline(
        """
        Pure-I/O enrichment per page: HTTP-fetch signals from the URL's
        source (YouTube oEmbed, arXiv Atom API, article HTML meta) and
        cache as enrichment_json on the queue_items row. Failure-tolerant
        — any per-source HTTP error collapses to empty signals; the asset
        always succeeds. Consumed by `triaged` for content_shape
        classification.
        """
    ),
)
def enriched(
    context: dg.AssetExecutionContext,
    config: EnrichedInput,
    triage_store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    content_type = classify_content_type(config.url)
    signals = enrich_url(config.url, content_type)

    triage_store.ensure_schema()
    enrichment_json = signals.to_json()
    triage_store.upsert_enriched(
        notion_page_id=page_id,
        url=config.url,
        enrichment_json=enrichment_json,
    )

    fetched = [
        name
        for name, src in (
            ("youtube", signals.youtube),
            ("arxiv", signals.arxiv),
            ("article", signals.article),
        )
        if src is not None
    ]
    return dg.MaterializeResult(
        metadata={
            "url": dg.MetadataValue.url(config.url),
            "content_type": dg.MetadataValue.text(content_type),
            "signals_fetched": dg.MetadataValue.text(", ".join(fetched) or "(none)"),
            "enrichment_chars": dg.MetadataValue.int(len(enrichment_json)),
            "enrichment_json": dg.MetadataValue.json(json.loads(enrichment_json)),
        }
    )


class TriageInput(dg.Config):
    """Typed input for the triage asset. Sensor populates from Notion;
    manual UI launches must supply via the Launchpad config form.

    `content_type`, `content_shape`, and `name` are user overrides — set
    on the Notion row before triage runs. Empty / typo'd `content_type`
    falls back to URL classification; empty / typo'd `content_shape`
    falls back to the rules classifier. `name` is metadata-only (passes
    through to the run materialization for observability; triage doesn't
    write it back).
    `added_at_iso` is an Added At backfill — the sensor sets it to the
    Notion page's `created_time` when the row has no Added At (mobile
    captures often omit it); None means "leave Added At alone."
    `raw_content_override` carries the user-pasted Notion page body when
    the row has `Use page body` ticked — sensor converts blocks
    to markdown and passes them through; default empty string means the
    downstream fetcher dispatches on the URL instead of the pasted body."""

    url: str
    content_type: str | None = None
    content_shape: str | None = None
    name: str | None = None
    added_at_iso: str | None = None
    # User-set Notion "Publish Date" — the manual override (and the fallback for
    # types the fetcher can't auto-date: PDF, podcast, date-less sites). Wins over
    # the fetcher's auto-detected date, which fills only when this is blank.
    publish_date_iso: str | None = None
    raw_content_override: str = ""


@dg.asset(
    key=["triage_knowledge_queue", "triaged"],
    group_name=GROUP_NAME,
    kinds={"notion", "sqlite"},
    code_version=TRIAGE_KNOWLEDGE_QUEUE_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    deps=[
        dg.AssetDep("notion_queue"),
        dg.AssetDep(["triage_knowledge_queue", "enriched"]),
    ],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=_oneline(
        """
        Per-row: fetch URL meta → classify content_type + content_shape
        (consumes `enriched`'s cache) → dedup → commit to queue.db + Notion.
        User can override Content Type / Content Shape SELECTs on the
        Notion row; valid overrides win over the classifier. Notion Status
        write is the last API call so the extract sensor never sees a
        partially-triaged row. See the pipeline README for the state
        machine + dedup / Name-seeding semantics.
        """
    ),
)
def triaged(
    context: dg.AssetExecutionContext,
    config: TriageInput,
    triage_notion: NotionQueueResource,
    triage_store: QueueStoreResource,
    content_shape_classifier: ContentShapeClassifier,
) -> dg.MaterializeResult:
    page_id = context.partition_key

    # Best-effort URL enrichment: follow redirects, extract page title + short
    # description from HTML head. Never raises — empty meta on any failure.
    meta = fetch_url_meta(config.url)
    effective_url = meta.redirected_url or config.url
    canonical = normalize_url(effective_url)
    # User override wins if set + valid; typo / empty → URL classifier.
    if config.content_type and config.content_type in ALL_CONTENT_TYPES:
        content_type = config.content_type
        content_type_source = "notion"
    else:
        content_type = classify_content_type(canonical)
        content_type_source = "classified"

    # Podcast audio URLs: look up a YouTube equivalent. On hit, substitute
    # the canonical URL and reclassify as YouTube so downstream fetcher
    # dispatch returns a free transcript instead of paying for Whisper.
    podcast_substituted_to: str | None = None
    if content_type == CONTENT_TYPE_PODCAST:
        audio_title_for_lookup = config.name or meta.title or ""
        substituted = maybe_redirect_podcast_to_youtube(
            audio_url=canonical,
            audio_title=audio_title_for_lookup,
        )
        if substituted:
            canonical = normalize_url(substituted)
            content_type = CONTENT_TYPE_YOUTUBE
            podcast_substituted_to = canonical

    triage_store.ensure_schema()

    # Dedup by canonical_url: a second Notion capture of an already-queued
    # URL is flagged Skipped with a pointer to the original. Excluding the
    # current page_id keeps re-triage on the same row (Re-Queued from
    # Failed) idempotent. No queue.db row is written for the dup so the
    # original cohort stays the single source of truth.
    dup_page_id = triage_store.find_canonical_url_duplicate(
        canonical_url=canonical,
        excluding_page_id=page_id,
    )
    if dup_page_id:
        dup_name = triage_notion.get_page_name(dup_page_id) or "(untitled)"
        dup_notion_url = f"https://www.notion.so/{dup_page_id.replace('-', '')}"
        triage_notion.update_status_skipped(
            page_id,
            [
                ("Duplicate of ", None),
                (dup_name, dup_notion_url),
                (" — ", None),
                (canonical, canonical),
            ],
        )
        return dg.MaterializeResult(
            metadata={
                "outcome": dg.MetadataValue.text("duplicate"),
                "duplicate_of": dg.MetadataValue.text(dup_page_id),
                "duplicate_of_name": dg.MetadataValue.text(dup_name),
                "duplicate_of_url": dg.MetadataValue.url(dup_notion_url),
                "canonical_url": dg.MetadataValue.url(canonical),
                "original_url": dg.MetadataValue.url(config.url),
                "content_type": dg.MetadataValue.text(content_type),
                "status_after": dg.MetadataValue.text("Skipped"),
                "summary": dg.MetadataValue.md(f"**Duplicate** of [{dup_name}]({dup_notion_url})"),
            }
        )

    existing_row = triage_store.get_row(notion_page_id=page_id)
    enrichment_json = (existing_row or {}).get("enrichment_json")
    enrichment = EnrichmentSignals.from_json(enrichment_json)
    # Same override-vs-classifier shape as content_type above.
    llm_meta: dict | None = None
    llm_duration_ms: int | None = None
    if config.content_shape and config.content_shape in ALL_CONTENT_SHAPES:
        content_shape = config.content_shape
        content_shape_source = "notion"
    elif content_type == CONTENT_TYPE_ARXIV:
        # arXiv URLs host research papers; no reason to round-trip the LLM.
        content_shape = SHAPE_RESEARCH_SUMMARY
        content_shape_source = "url_fastpath"
    elif content_type == CONTENT_TYPE_PODCAST:
        content_shape = SHAPE_PODCAST_EPISODE
        content_shape_source = "url_fastpath"
    else:
        t0 = time.monotonic()
        content_shape, llm_meta = content_shape_classifier.classify(
            enrichment=enrichment,
            content_type=content_type,
            url=canonical,
        )
        llm_duration_ms = int((time.monotonic() - t0) * 1000)
        content_shape_source = "llm_classified" if content_shape != SHAPE_UNKNOWN else "unknown"

    comments = triage_notion.get_page_comments(page_id)
    user_comments_json = json.dumps(comments) if comments else None

    triage_store.upsert_triaged(
        notion_page_id=page_id,
        url=config.url,
        canonical_url=canonical,
        content_type=content_type,
        content_shape=content_shape,
        raw_content_override=config.raw_content_override,
        user_comments_json=user_comments_json,
        content_date=config.publish_date_iso,
    )
    # Per-content-type display sources avoid YouTube's '- YouTube' static
    # title and generic og:description boilerplate. See display.py.
    display_title = resolve_display_title(content_type=content_type, enrichment=enrichment)
    display_description = resolve_display_description(
        content_type=content_type, enrichment=enrichment
    )
    # Only seed Notion's Name when the user left it blank — never overwrite a
    # user-set title. Notion's auto-default ("New queued page", "Untitled")
    # counts as blank so triage can replace it with the real page title.
    # Description is operational and safe to (re)write.
    name_for_notion = (
        display_title if (not _is_user_set_name(config.name) and display_title) else None
    )
    status_after = "Fetching"
    triage_notion.write_triaged(
        page_id=page_id,
        content_type=content_type,
        content_shape=content_shape,
        canonical_url=canonical,
        status_after=status_after,
        name=name_for_notion,
        description=display_description,
        added_at_iso=config.added_at_iso,
    )

    metadata: dict[str, dg.MetadataValue] = {
        "outcome": dg.MetadataValue.text("fetching"),
        "content_type": dg.MetadataValue.text(content_type),
        "content_type_source": dg.MetadataValue.text(content_type_source),
        "content_shape": dg.MetadataValue.text(content_shape),
        "content_shape_source": dg.MetadataValue.text(content_shape_source),
        "canonical_url": dg.MetadataValue.url(canonical),
        "original_url": dg.MetadataValue.url(config.url),
        "redirected_url": dg.MetadataValue.url(effective_url),
        "name": dg.MetadataValue.text(config.name or ""),
        "fetched_title": dg.MetadataValue.text(meta.title or ""),
        "fetched_description": dg.MetadataValue.text(meta.description or ""),
        "status_after": dg.MetadataValue.text(status_after),
        "summary": dg.MetadataValue.md(
            f"**{content_type}** / {content_shape} → Notion {status_after}"
        ),
    }
    if podcast_substituted_to:
        metadata["podcast_substituted_to"] = dg.MetadataValue.url(podcast_substituted_to)
    if llm_meta is not None:
        metadata["content_shape_llm_status"] = dg.MetadataValue.text(llm_meta.get("status", ""))
        if "model" in llm_meta:
            metadata["content_shape_llm_model"] = dg.MetadataValue.text(llm_meta["model"])
        if llm_duration_ms is not None:
            metadata["content_shape_llm_duration_ms"] = dg.MetadataValue.int(llm_duration_ms)
    return dg.MaterializeResult(metadata=metadata)


all_assets = [enriched, triaged]
