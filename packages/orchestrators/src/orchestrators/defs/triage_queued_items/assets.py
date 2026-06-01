import dagster as dg

from orchestrators.config import TRIAGE_QUEUED_ITEMS_DAG_VERSION

from .classify import canonicalize_url, classify_content_type, is_tier_a
from .def_config import PIPELINE_TAG, queue_items_partition_def
from .resources import TitleFetcherResource, TriageNotionResource, TriageQueueStore

GROUP_NAME = "triage_queued_items"


class TriageInput(dg.Config):
    """Typed input for the triage assets. Sensor populates from Notion;
    manual UI launches must supply via the Launchpad config form."""

    url: str
    notion_name: str = ""


@dg.asset(
    key=["triage_queued_items", "classified"],
    group_name=GROUP_NAME,
    compute_kind="python",
    code_version=TRIAGE_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Pure measurement: classifies content_type and canonicalizes URL. "
        "Optionally fetches the page <title> via GET when Notion's Name is "
        "empty. No writes to Notion or local store — downstream `routed` "
        "asset does the persistence."
    ),
)
def classified(
    context: dg.AssetExecutionContext,
    config: TriageInput,
    title_fetcher: TitleFetcherResource,
) -> dg.MaterializeResult:
    canonical = canonicalize_url(config.url)
    content_type = classify_content_type(canonical)
    if config.notion_name:
        name_source = "notion"
        name = config.notion_name
    else:
        fetched_title = title_fetcher.fetch_title(canonical)
        name = fetched_title or ""
        name_source = "fetched" if fetched_title else "empty"

    return dg.MaterializeResult(
        metadata={
            "content_type": dg.MetadataValue.text(content_type),
            "canonical_url": dg.MetadataValue.url(canonical),
            "original_url": dg.MetadataValue.url(config.url),
            "name_source": dg.MetadataValue.text(name_source),
            "name": dg.MetadataValue.text(name),
            "tier": dg.MetadataValue.text("A" if is_tier_a(content_type) else "B"),
            "summary": dg.MetadataValue.md(
                f"**{content_type}** ({'Tier A' if is_tier_a(content_type) else 'Tier B'})"
            ),
        }
    )


@dg.asset(
    key=["triage_queued_items", "routed"],
    group_name=GROUP_NAME,
    compute_kind="notion",
    code_version=TRIAGE_QUEUED_ITEMS_DAG_VERSION,
    partitions_def=queue_items_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    deps=[dg.AssetDep(["triage_queued_items", "classified"])],
    description=(
        "Writes classification result to the local store + Notion. Tier B "
        "(Article/Other) → Notion Status=Ready (NA fetches at engagement). "
        "Tier A (YouTube/arXiv/PDF/Podcast) → Notion Status=Fetching "
        "(extract_complex_contents picks up). Notion Status write is the last "
        "API call so half-classified states can't be picked up by the "
        "extract sensor."
    ),
)
def routed(
    context: dg.AssetExecutionContext,
    config: TriageInput,
    triage_notion: TriageNotionResource,
    triage_store: TriageQueueStore,
    title_fetcher: TitleFetcherResource,
) -> dg.MaterializeResult:
    page_id = context.partition_key
    canonical = canonicalize_url(config.url)
    content_type = classify_content_type(canonical)

    name_for_notion: str | None = None
    if not config.notion_name:
        fetched = title_fetcher.fetch_title(canonical)
        if fetched:
            name_for_notion = fetched

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
        name_if_empty=name_for_notion,
        status_after=status_after,
    )
    return dg.MaterializeResult(
        metadata={
            "status_after": dg.MetadataValue.text(status_after),
            "content_type": dg.MetadataValue.text(content_type),
            "canonical_url": dg.MetadataValue.url(canonical),
            "name_written": dg.MetadataValue.text(name_for_notion or ""),
            "summary": dg.MetadataValue.md(f"**{content_type}** → Notion {status_after}"),
        }
    )


all_assets = [classified, routed]
