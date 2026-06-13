import json
import textwrap

import dagster as dg

from orchestrators.config import TRIAGE_QUEUED_ITEMS_DAG_VERSION
from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)

from .classify import (
    ALL_CONTENT_TYPES,
    CONTENT_TYPE_PODCAST,
    CONTENT_TYPE_YOUTUBE,
    classify_content_type,
    normalize_url,
)
from .content_shape import classify_content_shape
from .def_config import PIPELINE_TAG, queue_items_partition_def
from .enrich import EnrichmentSignals, enrich_url
from .podcast_canonicalize import maybe_redirect_podcast_to_youtube
from .url_meta import fetch_url_meta

GROUP_NAME = "triage_queued_items"


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
    key=["triage_queued_items", "enriched"],
    group_name=GROUP_NAME,
    compute_kind="http",
    code_version=TRIAGE_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    deps=[dg.AssetDep("notion_queue")],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=_oneline(
        """
        Pure-I/O enrichment per page: HTTP-fetch signals from the URL's
        source (YouTube oEmbed, arXiv Atom API, article HTML meta) and
        cache as enrichment_json on the queue_items row. Failure-tolerant
        — any per-source HTTP error collapses to empty signals; the asset
        always succeeds. Phase 3 wires the cached signals into the
        content_shape classifier; for now `triaged` runs in parallel and
        ignores this output.
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

    `content_type` and `name` are user overrides — set on the Notion row
    before triage runs. Empty / typo'd content_type falls back to URL
    classification. `name` is metadata-only (passes through to the run
    materialization for observability; triage doesn't write it back).
    `added_at_iso` is an Added At backfill — the sensor sets it to the
    Notion page's `created_time` when the row has no Added At (mobile
    captures often omit it); None means "leave Added At alone."
    `raw_content_override` carries the user-pasted Notion page body when
    the row has `Use page body as content` ticked — sensor converts blocks
    to markdown and passes them through; default empty string means the
    downstream fetcher dispatches on the URL instead of the pasted body."""

    url: str
    content_type: str | None = None
    name: str | None = None
    added_at_iso: str | None = None
    raw_content_override: str = ""


@dg.asset(
    key=["triage_queued_items", "triaged"],
    group_name=GROUP_NAME,
    compute_kind="notion",
    code_version=TRIAGE_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    deps=[
        dg.AssetDep("notion_queue"),
        dg.AssetDep(["triage_queued_items", "enriched"]),
    ],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=_oneline(
        """
        One row → fetch URL meta (title + short description, redirects
        followed) → classify URL, canonicalize, then commit to local store +
        Notion. Reads `enrichment_json` cached by the `enriched` sibling
        asset to classify `content_shape` (drives the extractor's per-shape
        prompt routing in Phase 5; written to queue.db only this phase —
        Notion Content Shape property lands in Phase 4). Two terminal
        Notion states: Status=Skipped if the canonical URL duplicates an
        existing queue_items row (queue.db unchanged, original cohort
        stays the single source); Status=Fetching otherwise, and
        extract_complex_contents picks the row up and routes the URL to
        the fetcher service (its article handler is a catch-all for
        anything not yt/arxiv/pdf/medium, so Article/Other still land
        somewhere). Notion Status write is the last API call so
        partially-triaged states can't be picked up by the extract
        sensor. Name is seeded from the fetched page title when the user
        left it blank — extract's `published` later upgrades it to
        `topic_card.extracted_title` once the LLM has produced a sharper
        read. Description is always written when the fetch produced one
        (extract overwrites on a hit).
        """
    ),
)
def triaged(
    context: dg.AssetExecutionContext,
    config: TriageInput,
    triage_notion: NotionQueueResource,
    triage_store: QueueStoreResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key

    # Best-effort URL enrichment: follow redirects, extract page title + short
    # description from HTML head. Never raises — empty meta on any failure.
    meta = fetch_url_meta(config.url)
    effective_url = meta.final_url or config.url
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

    # Load enrichment cached by the `enriched` sibling asset and classify
    # content_shape. `enriched` always lands a row first (same partition,
    # asset dep), so `get_row` will hit; defensive `from_json` collapses
    # any malformed / missing payload to empty signals → `unknown` shape.
    existing_row = triage_store.get_row(notion_page_id=page_id)
    enrichment_json = (existing_row or {}).get("enrichment_json")
    enrichment = EnrichmentSignals.from_json(enrichment_json)
    content_shape = classify_content_shape(
        enrichment=enrichment,
        content_type=content_type,
        url=canonical,
    )

    triage_store.upsert_triaged(
        notion_page_id=page_id,
        url=config.url,
        canonical_url=canonical,
        content_type=content_type,
        content_shape=content_shape,
        raw_content_override=config.raw_content_override,
    )
    # Only seed Notion's Name when the user left it blank — never overwrite a
    # user-set title. Description is operational and safe to (re)write.
    name_for_notion = meta.title if (not config.name and meta.title) else None
    status_after = "Fetching"
    triage_notion.write_triaged(
        page_id=page_id,
        content_type=content_type,
        canonical_url=canonical,
        status_after=status_after,
        name=name_for_notion,
        description=meta.description,
        added_at_iso=config.added_at_iso,
    )

    metadata: dict[str, dg.MetadataValue] = {
        "outcome": dg.MetadataValue.text("fetching"),
        "content_type": dg.MetadataValue.text(content_type),
        "content_type_source": dg.MetadataValue.text(content_type_source),
        "content_shape": dg.MetadataValue.text(content_shape),
        "canonical_url": dg.MetadataValue.url(canonical),
        "original_url": dg.MetadataValue.url(config.url),
        "final_url": dg.MetadataValue.url(effective_url),
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
    return dg.MaterializeResult(metadata=metadata)


all_assets = [enriched, triaged]
