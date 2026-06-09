"""Per-source asyncio.Semaphore registry.

Correctness-critical: this assumes the service runs in one uvicorn worker.
"""

import asyncio


SOURCE_LIMITS: dict[str, int] = {
    "arxiv": 1,
    "jina": 5,
    "youtube": 2,
}

_DEFAULT_LIMIT = 4
_semaphores: dict[str, asyncio.Semaphore] = {}


def get_semaphore(source: str) -> asyncio.Semaphore:
    """Return the semaphore for a source, creating it lazily."""
    semaphore = _semaphores.get(source)
    if semaphore is None:
        semaphore = asyncio.Semaphore(SOURCE_LIMITS.get(source, _DEFAULT_LIMIT))
        _semaphores[source] = semaphore
    return semaphore
