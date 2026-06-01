import dagster as dg

from orchestrators.config import TRIAGE_QUEUED_ITEMS_DAG_VERSION

from .classify import canonicalize_url, classify_content_type, is_tier_a
from .def_config import PIPELINE_TAG, queue_items_partition_def
from .resources import TitleFetcherResource, TriageNotionResource, TriageQueueStore

GROUP_NAME = "triage_queued_items"


class TriageInput(dg.Config):
    """Typed input for the triage asset. Sensor populates from Notion;
    manual UI launches must supply via the Launchpad config form."""

    url: str
    notion_name: str = ""


@dg.asset(
    key=["triage_queued_items", "triaged"],
    group_name=GROUP_NAME,
    compute_kind="notion",
    code_version=TRIAGE_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "One row → classify URL, canonicalize, resolve name, then commit to "
        "local store + Notion. Tier B (Article/Other) → Notion Status=Ready "
        "(NA fetches at engagement). Tier A (YouTube/arXiv/PDF/Podcast) → "
        "Notion Status=Fetching (extract_complex_contents picks up). Notion "
        "Status write is the last API call so partially-triaged states can't be "
        "picked up by the extract sensor."
    ),
)
def triaged(
    context: dg.AssetExecutionContext,
    config: TriageInput,
    triage_notion: TriageNotionResource,
    triage_store: TriageQueueStore,
    title_fetcher: TitleFetcherResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key

    canonical = canonicalize_url(config.url)
    content_type = classify_content_type(canonical)
    if config.notion_name:
        name_source = "notion"
        name = config.notion_name
    else:
        fetched_title = title_fetcher.fetch_title(canonical)
        name = fetched_title or ""
        name_source = "fetched" if fetched_title else "empty"

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
        name_if_empty=(name if name_source == "fetched" else None),
        status_after=status_after,
    )

    return dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(content_type),
            "canonical_url": dg.MetadataValue.url(canonical),
            "original_url": dg.MetadataValue.url(config.url),
            "name_source": dg.MetadataValue.text(name_source),
            "name": dg.MetadataValue.text(name),
            "tier": dg.MetadataValue.text("A" if is_tier_a(content_type) else "B"),
            "status_after": dg.MetadataValue.text(status_after),
            "summary": dg.MetadataValue.md(f"**{content_type}** → Notion {status_after}"),
        }
    )


all_assets = [triaged]
