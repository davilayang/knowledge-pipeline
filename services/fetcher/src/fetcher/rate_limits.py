"""Per-key asyncio.Semaphore registry.

Keyed by handler NAME or tier rate_limit_key (whichever cascade.py passes in).
Correctness-critical: this assumes the service runs in one uvicorn worker.
"""

import asyncio


LIMITS: dict[str, int] = {
    "arxiv": 1,
    "jina": 5,
    "youtube": 2,
}

_DEFAULT_LIMIT = 4
_semaphores: dict[str, asyncio.Semaphore] = {}


def get_semaphore(key: str) -> asyncio.Semaphore:
    """Return the semaphore for a key, creating it lazily."""
    semaphore = _semaphores.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(LIMITS.get(key, _DEFAULT_LIMIT))
        _semaphores[key] = semaphore
    return semaphore
