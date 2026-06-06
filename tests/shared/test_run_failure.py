"""Tests for the shared step_failure_message helper used by run-failure sensors."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrators.defs.shared.run_failure import step_failure_message


def _step_event(*, user_description: str | None, error_message: str | None) -> SimpleNamespace:
    user_failure_data = (
        SimpleNamespace(description=user_description) if user_description is not None else None
    )
    error = SimpleNamespace(message=error_message) if error_message is not None else None
    return SimpleNamespace(
        event_specific_data=SimpleNamespace(
            user_failure_data=user_failure_data,
            error=error,
        )
    )


def test_prefers_user_failure_data_description():
    """A raised dg.Failure(description=...) is exposed via user_failure_data;
    that is the cleanest description for the Notion Error column."""
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        _step_event(
            user_description="arXiv fetch failed: HTTPError: Page request resulted in HTTP 503",
            error_message="wrapped DagsterExecutionStepExecutionError",
        )
    ]
    context.failure_event.message = "Execution of run failed. Steps failed: [...]"
    assert (
        step_failure_message(context)
        == "arXiv fetch failed: HTTPError: Page request resulted in HTTP 503"
    )


def test_falls_back_to_error_message_when_no_user_failure_data():
    """Unhandled exceptions (not raised via dg.Failure) carry no user_failure_data
    but do carry the underlying error message."""
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        _step_event(user_description=None, error_message="KeyError: 'content_type'")
    ]
    context.failure_event.message = "Steps failed: [...]"
    assert step_failure_message(context) == "KeyError: 'content_type'"


def test_falls_back_to_run_failure_message_when_no_step_events():
    """Run-level failures with no step failure event (cancellation, launcher
    error) fall back to the run-level message so we still write something."""
    context = MagicMock()
    context.get_step_failure_events.return_value = []
    context.failure_event.message = "Run was canceled"
    assert step_failure_message(context) == "Run was canceled"


def test_handles_get_step_failure_events_raising():
    """If the Dagster context API itself raises (older versions, unexpected
    state), we degrade to the run-level message instead of crashing the sensor."""
    context = MagicMock()
    context.get_step_failure_events.side_effect = RuntimeError("nope")
    context.failure_event.message = "fallback message"
    assert step_failure_message(context) == "fallback message"


def test_empty_user_description_is_treated_as_missing():
    """A user_failure_data with empty-string description shouldn't shadow the
    underlying error message — empty isn't useful on the Notion row."""
    context = MagicMock()
    context.get_step_failure_events.return_value = [
        _step_event(user_description="", error_message="real error here")
    ]
    context.failure_event.message = "Steps failed: [...]"
    assert step_failure_message(context) == "real error here"
