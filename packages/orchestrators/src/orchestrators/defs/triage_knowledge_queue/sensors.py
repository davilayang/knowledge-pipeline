import dagster as dg

from orchestrators.defs.shared.queue_resources import NotionQueueResource
from orchestrators.defs.shared.run_failure import mark_notion_failed_from_run

from .assets import EnrichedInput, TriageInput
from .def_config import MAX_QUEUED_PER_TICK, SENSOR_MIN_INTERVAL_S, queue_items_partition_def
from .schedules import triage_knowledge_queue_job


@dg.sensor(
    job=triage_knowledge_queue_job,
    minimum_interval_seconds=SENSOR_MIN_INTERVAL_S,
    description=(
        "Polls Notion Knowledge OS Queue for Status=Queued OR Status is empty "
        "rows (mobile-share template bypass absorbed). Registers each row's "
        "notion_page_id as a dynamic partition and triggers triage_knowledge_queue. "
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

        # User-set Content Shape SELECT (None if unset); asset validates +
        # falls back to rules classifier on typos. Symmetric to Content Type.
        cs_prop = row.get("properties", {}).get("Content Shape", {}).get("select")
        existing_cs = cs_prop.get("name") if cs_prop else None

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

        # User-set "Publish Date" (None if unset). Most types are auto-dated at
        # fetch (Medium/YouTube/arXiv/Facebook/most articles); this is the manual
        # override + the fallback for the residual gaps (PDF, podcast, date-less
        # sites). Wins over the fetcher's date. Same date-property shape as Added At.
        publish_date_prop = row.get("properties", {}).get("Publish Date", {})
        publish_date_date = (
            publish_date_prop.get("date") if isinstance(publish_date_prop, dict) else None
        )
        publish_date_iso = publish_date_date.get("start") if publish_date_date else None

        use_body_prop = row.get("properties", {}).get("Use page body", {})
        use_body = bool(use_body_prop.get("checkbox", False))
        raw_content_override = triage_notion.get_page_body_markdown(page_id) if use_body else ""

        last_edited = row.get("last_edited_time") or ""
        page_ids.append(page_id)
        run_requests.append(
            dg.RunRequest(
                run_key=f"triage-{page_id}-{last_edited}",
                partition_key=page_id,
                tags={"notion_page_id": page_id},
                run_config=dg.RunConfig(
                    ops={
                        "triage_knowledge_queue__enriched": EnrichedInput(url=url),
                        "triage_knowledge_queue__triaged": TriageInput(
                            url=url,
                            content_type=existing_ct,
                            content_shape=existing_cs,
                            name=existing_name,
                            added_at_iso=added_at_iso,
                            publish_date_iso=publish_date_iso,
                            raw_content_override=raw_content_override,
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


@dg.run_failure_sensor(
    monitored_jobs=[triage_knowledge_queue_job],
    minimum_interval_seconds=60,
    description=(
        "On any triage_knowledge_queue partition failure, write Status=Failed + "
        "Error back to the Notion row."
    ),
)
def mark_notion_failed_on_triage(
    context: dg.RunFailureSensorContext, triage_notion: NotionQueueResource
) -> None:
    mark_notion_failed_from_run(context, triage_notion, fallback="triage run failed")


all_sensors = [poll_notion_for_triage, mark_notion_failed_on_triage]
