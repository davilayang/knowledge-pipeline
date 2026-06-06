"""Shared helpers for `@dg.run_failure_sensor` bodies.

Run-failure sensors receive a `RunFailureSensorContext` whose
`failure_event.message` is the *generic* `Steps failed: [...]` string.
The user-meaningful reason — the `dg.Failure(description=...)` text raised
by the op — lives on the step failure events, which the sensor has to ask
for explicitly. `step_failure_message` walks those events and returns the
sharpest message we can produce, with a safe fallback chain.
"""

from __future__ import annotations

from typing import Any


def step_failure_message(context: Any) -> str | None:
    """Best-effort underlying step error for a failed run.

    Order of preference:
    1. `user_failure_data.description` from the first step failure event —
       this is exactly the string passed to `dg.Failure(description=...)`.
    2. `error.message` from the same event — useful for unhandled exceptions
       (KeyError etc.) that weren't raised via `dg.Failure`.
    3. `context.failure_event.message` — fallback for cancellations / launcher
       errors where no step event exists.

    Wrapped in a try/except so a Dagster API change can't take the sensor
    down — the worst case is we write the generic run message to Notion."""
    try:
        step_events = list(context.get_step_failure_events() or [])
    except Exception:  # noqa: BLE001
        step_events = []

    if step_events:
        first = step_events[0]
        data = getattr(first, "event_specific_data", None)
        user_failure_data = getattr(data, "user_failure_data", None)
        description = getattr(user_failure_data, "description", None)
        if description:
            return description
        error = getattr(data, "error", None)
        message = getattr(error, "message", None)
        if message:
            return message

    return getattr(getattr(context, "failure_event", None), "message", None)
