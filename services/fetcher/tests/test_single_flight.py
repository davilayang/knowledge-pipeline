"""Tests for the per-URL single-flight lock dict."""

import asyncio

import pytest

from fetcher.single_flight import get_url_lock


pytestmark = pytest.mark.asyncio


async def test_returns_same_lock_for_same_url() -> None:
    a = get_url_lock("hash-A")
    b = get_url_lock("hash-A")
    assert a is b


async def test_returns_different_locks_for_different_urls() -> None:
    a = get_url_lock("hash-A")
    b = get_url_lock("hash-B")
    assert a is not b


async def test_lock_serializes_concurrent_holders() -> None:
    """Holding the lock blocks other awaiters."""
    order: list[str] = []

    async def worker(label: str, delay: float) -> None:
        lock = get_url_lock("hash-Z")
        async with lock:
            order.append(f"{label}-enter")
            await asyncio.sleep(delay)
            order.append(f"{label}-exit")

    await asyncio.gather(worker("A", 0.05), worker("B", 0.01))

    assert order in [
        ["A-enter", "A-exit", "B-enter", "B-exit"],
        ["B-enter", "B-exit", "A-enter", "A-exit"],
    ]


async def test_lock_self_gcs_when_no_holders() -> None:
    """After all holders release and references drop, a fresh acquire still works."""
    import gc

    lock = get_url_lock("hash-temp")
    async with lock:
        pass

    del lock
    gc.collect()

    new_lock = get_url_lock("hash-temp")
    async with new_lock:
        pass
