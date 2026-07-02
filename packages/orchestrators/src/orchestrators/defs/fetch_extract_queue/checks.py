import dagster as dg

from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

from .def_config import LIFECYCLE_DRIFT_AGE_MINUTES


@dg.asset_check(
    asset=dg.AssetKey(["fetch_extract_queue", "publish_item"]),
    name="notion_lifecycle_in_sync",
    blocking=False,
    description=(
        "Local store rows extracted more than LIFECYCLE_DRIFT_AGE_MINUTES ago "
        "should have Notion Status=Ready. Alerts when the lifecycle-flip leg "
        "is failing silently (extraction landed, Notion never updated)."
    ),
)
def notion_lifecycle_in_sync(
    context: dg.AssetCheckExecutionContext,
    store: QueueStoreResource,
    notion: NotionQueueResource,
) -> dg.AssetCheckResult:
    stale_local = store.list_with_stale_extraction(min_age_minutes=LIFECYCLE_DRIFT_AGE_MINUTES)
    out_of_sync: list[str] = []
    for row in stale_local:
        try:
            status = notion.get_status(row["notion_page_id"])
        except Exception as exc:
            context.log.warning("notion.get_status failed for %s: %s", row["notion_page_id"], exc)
            continue
        if status != "Ready":
            out_of_sync.append(row["notion_page_id"])
    return dg.AssetCheckResult(
        passed=not out_of_sync,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "out_of_sync_count": dg.MetadataValue.int(len(out_of_sync)),
            "out_of_sync_page_ids": dg.MetadataValue.json(out_of_sync[:20]),
            "checked_count": dg.MetadataValue.int(len(stale_local)),
        },
    )


all_checks = [notion_lifecycle_in_sync]
