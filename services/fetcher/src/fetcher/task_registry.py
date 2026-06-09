"""In-process registry for background fetch tasks.

This module carries a load-bearing service invariant:
The fetcher service MUST run with --workers 1. The per-job asyncio.Task
handles stored here are in-process only; multi-worker fan-out would
make tasks invisible to other processes and break cancellation.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# job_id -> asyncio.Task
_task_handles: dict[str, asyncio.Task] = {}


def register(job_id: str, task: asyncio.Task) -> None:
    """Register a new background task."""
    _task_handles[job_id] = task


def get(job_id: str) -> asyncio.Task | None:
    """Get a task handle by job_id."""
    return _task_handles.get(job_id)


def cancel(job_id: str) -> bool:
    """Cancel a task if it exists. Returns True if task was found."""
    task = _task_handles.get(job_id)
    if task:
        task.cancel()
        return True
    return False


def discard(job_id: str) -> None:
    """Remove a task handle from the registry."""
    _task_handles.pop(job_id, None)


def list_all() -> list[str]:
    """List all registered job_ids."""
    return list(_task_handles.keys())
