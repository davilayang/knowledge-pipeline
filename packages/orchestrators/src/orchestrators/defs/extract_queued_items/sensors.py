import dagster as dg

from .def_config import MAX_QUEUED_PER_TICK, SENSOR_MIN_INTERVAL_S, queue_items_partition_def
from .resources import NotionResource
from .schedules import extract_queued_items_job


@dg.sensor(
    job=extract_queued_items_job,
    minimum_interval_seconds=SENSOR_MIN_INTERVAL_S,
    description=(
        "Polls Notion Knowledge OS Queue for Status=Queued rows and triggers "
        "extract_queued_items partitioned on notion_page_id. Bounded by "
        "MAX_QUEUED_PER_TICK to stay under Notion rate limits during bursty "
        "captures."
    ),
)
def poll_notion_queue(
    context: dg.SensorEvaluationContext, notion: NotionResource
) -> dg.SensorResult:
    rows = notion.query_queue(status="Queued", page_size=MAX_QUEUED_PER_TICK)
    run_requests: list[dg.RunRequest] = []
    page_ids: list[str] = []
    for row in rows:
        page_id = row.get("id")
        if not page_id:
            continue
        url_prop = row.get("properties", {}).get("URL", {})
        url = url_prop.get("url")
        if not url:
            context.log.warning("Skipping page_id=%s with empty URL", page_id)
            continue
        last_edited = row.get("last_edited_time") or ""
        page_ids.append(page_id)
        run_requests.append(
            dg.RunRequest(
                run_key=f"queue-{page_id}-{last_edited}",
                partition_key=page_id,
                tags={"notion_page_id": page_id, "url": url},
            )
        )

    dynamic_requests = [queue_items_partition_def.build_add_request(page_ids)] if page_ids else []
    return dg.SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=dynamic_requests,
    )


def _handle_run_failure(
    *, run_tags: dict[str, str], failure_message: str | None, notion: NotionResource
) -> None:
    page_id = run_tags.get("notion_page_id")
    if not page_id:
        return
    notion.update_status_failed(page_id, failure_message or "run failed")


@dg.run_failure_sensor(
    monitored_jobs=[extract_queued_items_job],
    minimum_interval_seconds=60,
    description=(
        "On any extract_queued_items partition failure, write Status=Failed + "
        "Error back to the Notion row so the user sees the failure without "
        "needing to open Dagster."
    ),
)
def mark_notion_failed_on_run_failure(
    context: dg.RunFailureSensorContext, notion: NotionResource
) -> None:
    _handle_run_failure(
        run_tags=dict(context.dagster_run.tags),
        failure_message=context.failure_event.message,
        notion=notion,
    )


all_sensors = [poll_notion_queue, mark_notion_failed_on_run_failure]
