"""Shared body for `@dg.run_failure_sensor` handlers that write back to Notion."""

from __future__ import annotations

from typing import Any

import dagster as dg

from orchestrators.defs.shared.queue_resources import NotionQueueResource


def _failed_check_summary(context: Any) -> str | None:
    """The `summary` metadata of the first failed asset check in this run.

    A blocking check raises DagsterAssetCheckFailedError, which names the check
    and nothing else — what to do about it is in the check's own metadata, and
    that is what the operator needs in Notion.
    """
    try:
        entries = context.instance.all_logs(
            context.dagster_run.run_id, of_type=dg.DagsterEventType.ASSET_CHECK_EVALUATION
        )
    except Exception:
        return None
    for entry in entries or []:
        data = getattr(getattr(entry, "dagster_event", None), "event_specific_data", None)
        if data is None or getattr(data, "passed", True):
            continue
        summary = (getattr(data, "metadata", None) or {}).get("summary")
        text = (getattr(summary, "value", None) or "").strip()
        if text:
            return text
    return None


def step_failure_message(context: Any) -> str | None:
    """Underlying step error for a failed run.

    Prefers (in order): `user_failure_data.description` from the terminal
    step failure event (the `dg.Failure(description=...)` text), then a failed
    asset check's `summary`, then the step's raw `error.message`, then the
    run-level `failure_event.message`. Uses `step_events[-1]` so retried runs
    show the terminal cause, not the historical first attempt."""
    try:
        step_events = list(context.get_step_failure_events() or [])
    except AttributeError:
        step_events = []

    if step_events:
        data = getattr(step_events[-1], "event_specific_data", None)
        description = getattr(getattr(data, "user_failure_data", None), "description", None)
        if description:
            return description
        # Before the raw message: a blocking check's failure arrives as
        # Dagster's own wrapper, which says a check failed but not what to fix.
        check_summary = _failed_check_summary(context)
        if check_summary:
            return check_summary

        # Dagster wraps op exceptions in DagsterExecutionStepExecutionError
        # ("Error occurred while executing op ..."); the real exception is
        # deeper in the `cause` chain. Take the innermost link that actually
        # carries a message, so a bare/empty root doesn't hide its parent.
        error = getattr(data, "error", None)
        message = None
        while error is not None:
            candidate = (getattr(error, "message", None) or "").strip()
            if candidate:
                message = candidate
            error = getattr(error, "cause", None)
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
