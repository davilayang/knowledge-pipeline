"""Shared body for `@dg.run_failure_sensor` handlers that write back to Notion."""

from __future__ import annotations

from typing import Any

from orchestrators.defs.shared.queue_resources import NotionQueueResource


def step_failure_message(context: Any) -> str | None:
    """Underlying step error for a failed run.

    Prefers (in order): `user_failure_data.description` from the terminal
    step failure event (the `dg.Failure(description=...)` text), then the
    step's raw `error.message`, then the run-level `failure_event.message`.
    Uses `step_events[-1]` so retried runs show the terminal cause, not the
    historical first attempt."""
    try:
        step_events = list(context.get_step_failure_events() or [])
    except AttributeError:
        step_events = []

    if step_events:
        data = getattr(step_events[-1], "event_specific_data", None)
        description = getattr(getattr(data, "user_failure_data", None), "description", None)
        if description:
            return description
        message = getattr(getattr(data, "error", None), "message", None)
        if message:
            return message

    return context.failure_event.message


def mark_notion_failed_from_run(
    context: Any,
    notion: NotionQueueResource,
    *,
    fallback: str = "run failed",
) -> None:
    """Body for run-failure sensors monitoring a partitioned Notion job.

    Pulls `notion_page_id` from run tags (no-op if absent — non-Notion run),
    resolves the sharpest step failure message, and writes Status=Failed +
    Error back to the row."""
    page_id = dict(context.dagster_run.tags).get("notion_page_id")
    if not page_id:
        return
    notion.update_status_failed(page_id, step_failure_message(context) or fallback)
