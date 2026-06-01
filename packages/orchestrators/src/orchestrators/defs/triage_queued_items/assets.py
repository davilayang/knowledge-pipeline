import dagster as dg

from orchestrators.config import TRIAGE_QUEUED_ITEMS_DAG_VERSION

from .classify import ALL_CONTENT_TYPES, canonicalize_url, classify_content_type, is_tier_a
from .def_config import PIPELINE_TAG, queue_items_partition_def
from .resources import TriageNotionResource, TriageQueueStore

GROUP_NAME = "triage_queued_items"


class TriageInput(dg.Config):
    """Typed input for the triage asset. Sensor populates from Notion;
    manual UI launches must supply via the Launchpad config form.

    `content_type` and `name` are user overrides — set on the Notion row
    before triage runs. Empty / typo'd content_type falls back to URL
    classification. `name` is metadata-only (passes through to the run
    materialization for observability; triage doesn't write it back)."""

    url: str
    content_type: str | None = None
    name: str | None = None


@dg.asset(
    key=["triage_queued_items", "triaged"],
    group_name=GROUP_NAME,
    compute_kind="notion",
    code_version=TRIAGE_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "One row → classify URL, canonicalize, then commit to local store + "
        "Notion. Tier B (Article/Other) → Notion Status=Ready (NA fetches at "
        "engagement). Tier A (YouTube/arXiv/PDF/Podcast) → Notion "
        "Status=Fetching (extract_complex_contents picks up). Notion Status "
        "write is the last API call so partially-triaged states can't be "
        "picked up by the extract sensor. Name is left untouched — extract "
        "(Tier A) or NA (Tier B) fills it from real content."
    ),
)
def triaged(
    context: dg.AssetExecutionContext,
    config: TriageInput,
    triage_notion: TriageNotionResource,
    triage_store: TriageQueueStore,
) -> dg.MaterializeResult:
    page_id = context.partition_key

    canonical = canonicalize_url(config.url)
    # User override wins if set + valid; typo / empty → URL classifier.
    if config.content_type and config.content_type in ALL_CONTENT_TYPES:
        content_type = config.content_type
        content_type_source = "notion"
    else:
        content_type = classify_content_type(canonical)
        content_type_source = "classified"

    triage_store.ensure_schema()
    triage_store.upsert_triaged(
        notion_page_id=page_id,
        url=config.url,
        canonical_url=canonical,
        content_type=content_type,
    )
    status_after = "Fetching" if is_tier_a(content_type) else "Ready"
    triage_notion.write_triaged(
        page_id=page_id,
        content_type=content_type,
        status_after=status_after,
    )

    return dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(content_type),
            "content_type_source": dg.MetadataValue.text(content_type_source),
            "canonical_url": dg.MetadataValue.url(canonical),
            "original_url": dg.MetadataValue.url(config.url),
            "name": dg.MetadataValue.text(config.name or ""),
            "tier": dg.MetadataValue.text("A" if is_tier_a(content_type) else "B"),
            "status_after": dg.MetadataValue.text(status_after),
            "summary": dg.MetadataValue.md(f"**{content_type}** → Notion {status_after}"),
        }
    )


all_assets = [triaged]
