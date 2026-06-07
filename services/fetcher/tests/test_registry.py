"""Tests for source registry and cascade engine."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.registry import REGISTERED_SOURCES, find_source, run_cascade
from fetcher.types import FetchContext, RawTierResult, Tier


def test_registered_sources_order() -> None:
    assert [source.NAME for source in REGISTERED_SOURCES] == ["arxiv", "youtube", "article"]


def test_find_source_routes_specific_before_article() -> None:
    assert find_source("https://arxiv.org/abs/2401.00001").NAME == "arxiv"
    assert find_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ").NAME == "youtube"
    assert find_source("https://example.com/post").NAME == "article"
    assert find_source("mailto:test@example.com") is None


@pytest.mark.asyncio
async def test_cascade_returns_first_passing_free_tier() -> None:
    ctx = MagicMock(spec=FetchContext)
    first = Tier(
        "first",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="x" * 50, status=200)),
    )
    second = Tier(
        "second",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="unused", status=200)),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [first, second]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert result.tier_used == "first"
    second.run.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_escalates_to_paid_when_allowed() -> None:
    ctx = MagicMock(spec=FetchContext)
    free = Tier(
        "free",
        "free",
        10,
        10**9,
        AsyncMock(return_value=RawTierResult(content="some content", status=200)),
    )
    paid = Tier(
        "paid",
        "paid",
        10,
        10,
        AsyncMock(return_value=RawTierResult(content="rich content", status=200)),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [free, paid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="high", allow_paid=True)
    assert result.tier_used == "paid"


@pytest.mark.asyncio
async def test_cascade_strict_paid_tier_raises() -> None:
    ctx = MagicMock(spec=FetchContext)
    free = Tier(
        "free",
        "free",
        10,
        10**9,
        AsyncMock(return_value=RawTierResult(content="some content", status=200)),
    )
    paid = Tier(
        "paid",
        "paid",
        10,
        10,
        AsyncMock(side_effect=ValueError("LlamaParse failed")),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = True
        TIERS = [free, paid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    with pytest.raises(ValueError, match="LlamaParse"):
        await run_cascade(FakeSource, ctx, "https://x", quality="high", allow_paid=True)
