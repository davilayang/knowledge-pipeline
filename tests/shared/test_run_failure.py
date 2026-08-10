"""Tests for shared run-failure sensor helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrators.defs.shared.run_failure import (
    mark_notion_failed_from_run,
    step_failure_message,
)


def _step_event(*, user_description: str | None, error_message: str | None) -> SimpleNamespace:
    user_failure_data = (
        SimpleNamespace(description=user_description) if user_description is not None else None
    )
    error = SimpleNamespace(message=error_message) if error_message is not None else None
    return SimpleNamespace(
        event_specific_data=SimpleNamespace(user_failure_data=user_failure_data, error=error)
    )


def test_step_failure_message_uses_terminal_step_not_first():
    """On a retried run, we want the terminal failure (what actually gave up),
    not the historical first attempt that may have been transient."""
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        _step_event(user_description="arXiv 503", error_message=None),
        _step_event(user_description="arXiv 503 again", error_message=None),
        _step_event(user_description="LlamaParse rejected PDF", error_message=None),
    ]
    context.failure_event.message = "Steps failed: [...]"
    assert step_failure_message(context) == "LlamaParse rejected PDF"


def test_step_failure_message_falls_back_to_error_message_when_no_user_failure_data():
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        _step_event(user_description=None, error_message="KeyError: 'content_type'")
    ]
    context.failure_event.message = "Steps failed: [...]"
    assert step_failure_message(context) == "KeyError: 'content_type'"


def test_step_failure_message_unwraps_dagster_wrapper_to_root_cause():
    """Dagster wraps op exceptions in DagsterExecutionStepExecutionError; the
    Notion row should show the underlying exception, not the wrapper."""
    root = SimpleNamespace(message="openai.RateLimitError: quota exceeded\n", cause=None)
    wrapper = SimpleNamespace(
        message=(
            "dagster._core.errors.DagsterExecutionStepExecutionError: "
            'Error occurred while executing op "extract_reading_card"\n'
        ),
        cause=root,
    )
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        SimpleNamespace(event_specific_data=SimpleNamespace(user_failure_data=None, error=wrapper))
    ]
    context.failure_event.message = "Steps failed: [...]"
    assert step_failure_message(context) == "openai.RateLimitError: quota exceeded"


def test_step_failure_message_skips_message_less_root_cause():
    """A root cause with no message (e.g. a bare httpx timeout serialized empty)
    must not hide the informative link above it."""
    root = SimpleNamespace(message="   ", cause=None)
    middle = SimpleNamespace(message="httpx.ReadTimeout: fetching arxiv.org\n", cause=root)
    wrapper = SimpleNamespace(message="DagsterExecutionStepExecutionError: ...", cause=middle)
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        SimpleNamespace(event_specific_data=SimpleNamespace(user_failure_data=None, error=wrapper))
    ]
    context.failure_event.message = "Steps failed: [...]"
    assert step_failure_message(context) == "httpx.ReadTimeout: fetching arxiv.org"


def test_step_failure_message_falls_back_to_run_failure_when_no_step_events():
    context = MagicMock()
    context.get_step_failure_events.return_value = []
    context.failure_event.message = "Run was canceled"
    assert step_failure_message(context) == "Run was canceled"


def test_step_failure_message_survives_get_step_failure_events_raising():
    context = MagicMock()
    context.get_step_failure_events.side_effect = AttributeError("api removed")
    context.failure_event.message = "fallback"
    assert step_failure_message(context) == "fallback"


def test_mark_notion_failed_from_run_writes_with_page_id_from_tag():
    context = MagicMock()
    context.dagster_run.tags = {"notion_page_id": "p-1"}
    context.get_step_failure_events.return_value = [
        _step_event(user_description="arXiv fetch failed: HTTP 503", error_message=None)
    ]
    notion = MagicMock()
    mark_notion_failed_from_run(context, notion)
    notion.update_status_failed.assert_called_once_with("p-1", "arXiv fetch failed: HTTP 503")


def test_mark_notion_failed_from_run_noop_when_run_lacks_page_id():
    context = MagicMock()
    context.dagster_run.tags = {}
    notion = MagicMock()
    mark_notion_failed_from_run(context, notion)
    notion.update_status_failed.assert_not_called()


def test_mark_notion_failed_from_run_uses_fallback_when_no_message():
    context = MagicMock()
    context.dagster_run.tags = {"notion_page_id": "p-1"}
    context.get_step_failure_events.return_value = []
    context.failure_event.message = None
    notion = MagicMock()
    mark_notion_failed_from_run(context, notion, fallback="triage run failed")
    notion.update_status_failed.assert_called_once_with("p-1", "triage run failed")
