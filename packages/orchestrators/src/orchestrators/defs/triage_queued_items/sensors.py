import dagster as dg

from orchestrators.defs.shared.queue_resources import NotionQueueResource

from .assets import TriageInput
from .def_config import MAX_QUEUED_PER_TICK, SENSOR_MIN_INTERVAL_S, queue_items_partition_def
from .schedules import triage_queued_items_job


@dg.sensor(
    job=triage_queued_items_job,
    minimum_interval_seconds=SENSOR_MIN_INTERVAL_S,
    description=(
        "Polls Notion Knowledge OS Queue for Status=Queued OR Status is empty "
        "rows (mobile-share template bypass absorbed). Registers each row's "
        "notion_page_id as a dynamic partition and triggers triage_queued_items. "
        "Bounded by MAX_QUEUED_PER_TICK."
    ),
)
def poll_notion_for_triage(
    context: dg.SensorEvaluationContext, triage_notion: NotionQueueResource
) -> dg.SensorResult:
    # Rows with either Empty or Queued status
    rows = triage_notion.query_for_triage(page_size=MAX_QUEUED_PER_TICK)

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

        # User-set Content Type SELECT (None if unset); asset validates +
        # falls back to URL classifier on typos.
        ct_prop = row.get("properties", {}).get("Content Type", {}).get("select")
        existing_ct = ct_prop.get("name") if ct_prop else None

        # User-set Name (None if unset); metadata-only passthrough.
        name_chunks = row.get("properties", {}).get("Name", {}).get("title") or []
        existing_name = "".join(c.get("plain_text") or "" for c in name_chunks).strip() or None

        # Mobile capture surfaces (iOS Share-to-Notion, Web Clipper) often skip
        # the Added At property. Backfill from the page's created_time so the
        # Queue's chronological view stays sorted; leave alone if the user (or
        # capture) already set it.
        added_at_iso: str | None = None
        added_at_prop = row.get("properties", {}).get("Added At", {})
        added_at_date = added_at_prop.get("date") if isinstance(added_at_prop, dict) else None
        existing_added_at = added_at_date.get("start") if added_at_date else None
        if not existing_added_at:
            added_at_iso = row.get("created_time") or None

        last_edited = row.get("last_edited_time") or ""
        page_ids.append(page_id)
        run_requests.append(
            dg.RunRequest(
                run_key=f"triage-{page_id}-{last_edited}",
                partition_key=page_id,
                tags={"notion_page_id": page_id},
                run_config=dg.RunConfig(
                    ops={
                        "triage_queued_items__triaged": TriageInput(
                            url=url,
                            content_type=existing_ct,
                            name=existing_name,
                            added_at_iso=added_at_iso,
                        ),
                    }
                ),
            )
        )

    dynamic_requests = [queue_items_partition_def.build_add_request(page_ids)] if page_ids else []
    return dg.SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=dynamic_requests,
    )


def _handle_run_failure(
    *,
    run_tags: dict[str, str],
    failure_message: str | None,
    triage_notion: NotionQueueResource,
) -> None:
    page_id = run_tags.get("notion_page_id")
    if not page_id:
        return
    triage_notion.update_status_failed(page_id, failure_message or "triage run failed")


@dg.run_failure_sensor(
    monitored_jobs=[triage_queued_items_job],
    minimum_interval_seconds=60,
    description=(
        "On any triage_queued_items partition failure, write Status=Failed + "
        "Error back to the Notion row."
    ),
)
def mark_notion_failed_on_triage(
    context: dg.RunFailureSensorContext, triage_notion: NotionQueueResource
) -> None:
    _handle_run_failure(
        run_tags=dict(context.dagster_run.tags),
        failure_message=context.failure_event.message,
        triage_notion=triage_notion,
    )


all_sensors = [poll_notion_for_triage, mark_notion_failed_on_triage]
