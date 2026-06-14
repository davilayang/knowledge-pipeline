"""Tests for fetcher.rate_limits."""

import asyncio

import pytest

from fetcher.rate_limits import LIMITS, get_semaphore


def test_known_sources_have_explicit_limits() -> None:
    assert LIMITS["arxiv"] == 1
    assert LIMITS["jina"] == 5
    assert LIMITS["youtube"] == 2


@pytest.mark.asyncio
async def test_get_semaphore_returns_same_instance_for_same_source() -> None:
    a = get_semaphore("arxiv")
    b = get_semaphore("arxiv")
    assert a is b


@pytest.mark.asyncio
async def test_unknown_source_gets_default_semaphore() -> None:
    """Unknown sources fall back to a real Semaphore (not None), so they
    aren't accidentally unbounded. The exact default count is implementation
    detail and shouldn't break this test on tuning."""
    semaphore = get_semaphore("brand-new-source")
    assert isinstance(semaphore, asyncio.Semaphore)


@pytest.mark.asyncio
async def test_arxiv_semaphore_is_single_flight() -> None:
    """arxiv: Semaphore(1), only one coroutine can hold it at a time."""
    semaphore = get_semaphore("arxiv")
    order: list[str] = []

    async def worker(label: str, delay: float) -> None:
        async with semaphore:
            order.append(f"{label}-enter")
            await asyncio.sleep(delay)
            order.append(f"{label}-exit")

    await asyncio.gather(worker("A", 0.05), worker("B", 0.01))

    assert order in [
        ["A-enter", "A-exit", "B-enter", "B-exit"],
        ["B-enter", "B-exit", "A-enter", "A-exit"],
    ]
