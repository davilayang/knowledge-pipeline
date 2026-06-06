import dagster as dg

from orchestrators.defs.shared.queue_resources import NotionQueueResource
from orchestrators.defs.shared.run_failure import step_failure_message

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

    # Return rows ready to be fetched
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


def _handle_run_failure(
    *, run_tags: dict[str, str], failure_message: str | None, notion: NotionQueueResource
) -> None:
    page_id = run_tags.get("notion_page_id")
    if not page_id:
        return
    notion.update_status_failed(page_id, failure_message or "run failed")


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
    _handle_run_failure(
        run_tags=dict(context.dagster_run.tags),
        failure_message=step_failure_message(context),
        notion=notion,
    )


all_sensors = [poll_notion_for_extract, mark_notion_failed_on_extract]
