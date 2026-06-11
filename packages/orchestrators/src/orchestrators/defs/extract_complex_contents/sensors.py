import dagster as dg

from orchestrators.defs.shared.queue_resources import NotionQueueResource
from orchestrators.defs.shared.run_failure import mark_notion_failed_from_run

from .def_config import MAX_TO_EXTRACT_PER_TICK, SENSOR_MIN_INTERVAL_S, SUPPORTED_CONTENT_TYPES
from .schedules import extract_complex_contents_job


@dg.sensor(
    job=extract_complex_contents_job,
    minimum_interval_seconds=SENSOR_MIN_INTERVAL_S,
    description=(
        "Polls Notion Knowledge OS Queue for Status=Fetching rows with Content Type ∈ "
        "SUPPORTED_CONTENT_TYPES; triggers extract_complex_contents partitioned on "
        "notion_page_id. Bounded by MAX_TO_EXTRACT_PER_TICK. Triage registers the dynamic "
        "partition before this pipeline picks up."
    ),
)
def poll_notion_for_extract(
    context: dg.SensorEvaluationContext, notion: NotionQueueResource
) -> dg.SensorResult:

    context.log.info("polling Notion queue_db_id=%s for Status=Fetching", notion.queue_db_id)
    rows = notion.query_for_extract(
        page_size=MAX_TO_EXTRACT_PER_TICK,
        supported_content_types=SUPPORTED_CONTENT_TYPES,
    )
    run_requests: list[dg.RunRequest] = []
    for row in rows:
        page_id = row.get("id")
        if not page_id:
            continue
        url_prop = row.get("properties", {}).get("URL", {})
        url = url_prop.get("url")
        if not url:
            context.log.warning("Skipping page_id=%s with empty URL", page_id)
            continue
        content_type_select = row.get("properties", {}).get("Content Type", {}).get("select") or {}
        content_type = content_type_select.get("name")
        if not content_type:
            context.log.warning("Skipping page_id=%s with missing Content Type", page_id)
            continue
        last_edited = row.get("last_edited_time") or ""
        run_requests.append(
            dg.RunRequest(
                run_key=f"queue-{page_id}-{last_edited}",
                partition_key=page_id,
                tags={"notion_page_id": page_id, "url": url, "content_type": content_type},
            )
        )

    return dg.SensorResult(run_requests=run_requests)


@dg.run_failure_sensor(
    monitored_jobs=[extract_complex_contents_job],
    minimum_interval_seconds=60,
    description=(
        "On any extract_complex_contents partition failure, write Status=Failed + "
        "Error back to the Notion row so the user sees the failure without "
        "needing to open Dagster."
    ),
)
def mark_notion_failed_on_extract(
    context: dg.RunFailureSensorContext, notion: NotionQueueResource
) -> None:
    mark_notion_failed_from_run(context, notion)


all_sensors = [poll_notion_for_extract, mark_notion_failed_on_extract]
