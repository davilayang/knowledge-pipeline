"""Per-URL asyncio.Lock dict with weakref GC.

Correctness-critical: this assumes the service runs in one uvicorn worker.
"""

import asyncio
import weakref


_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def get_url_lock(url_hash: str) -> asyncio.Lock:
    """Return the single-process lock for this canonical URL hash."""
    lock = _locks.get(url_hash)
    if lock is None:
        lock = asyncio.Lock()
        _locks[url_hash] = lock
    return lock
