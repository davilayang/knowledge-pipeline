"""Tests for the cascade engine."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.cascade import run_cascade
from fetcher.types import FetchContext, RawTierResult, Tier


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
async def test_cascade_carries_metadata_from_below_floor_tier() -> None:
    # Paywalled Medium: the free (Jina) tier fetches the preamble metadata
    # (title + published date) but its BODY is below floor, so a paid tier wins on
    # content. The date must survive onto the winning result, not be discarded.
    ctx = MagicMock(spec=FetchContext)
    free = Tier(
        "free",
        "free",
        10000,  # floor above the short body → below floor
        10**9,
        AsyncMock(
            return_value=RawTierResult(
                content="x" * 100,
                status=200,
                metadata={"title": "T", "published": "2026-03-01"},
            )
        ),
    )
    paid = Tier(
        "paid",
        "paid",
        10,
        10,
        AsyncMock(return_value=RawTierResult(content="rich content body", status=200, metadata={})),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [free, paid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=True)
    assert result.tier_used == "paid"
    assert result.content == "rich content body"
    assert result.metadata == {"title": "T", "published": "2026-03-01"}


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
async def test_cascade_returns_empty_when_all_tiers_fail_validation() -> None:
    """If every tier returns content that fails its validate gate, cascade returns empty.

    Without this, the legacy "longest wins" fallback would have returned partial
    or paywall-marker content as a soft 200, hiding the real upstream failure.
    """
    ctx = MagicMock(spec=FetchContext)

    def rejects_everything(_content: str) -> bool:
        return False

    first = Tier(
        "first",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="partial 1", status=200)),
        validate=rejects_everything,
    )
    second = Tier(
        "second",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="partial 2 longer", status=200)),
        validate=rejects_everything,
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [first, second]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert result.content == ""
    assert result.tier_used == ""
    # tier_log still records both attempts so the caller knows what happened.
    assert [entry.tier for entry in result.tier_log] == ["first", "second"]


@pytest.mark.asyncio
async def test_cascade_picks_longest_validated_when_none_meet_floor() -> None:
    """Among tiers that pass validation but miss the high_chars floor, the longest wins.

    Validation is the hard gate; floor is the quality preference.
    """
    ctx = MagicMock(spec=FetchContext)

    def accepts_everything(_content: str) -> bool:
        return True

    short_valid = Tier(
        "short",
        "free",
        10,
        10000,
        AsyncMock(return_value=RawTierResult(content="x" * 100, status=200)),
        validate=accepts_everything,
    )
    longer_valid = Tier(
        "longer",
        "free",
        10,
        10000,
        AsyncMock(return_value=RawTierResult(content="y" * 500, status=200)),
        validate=accepts_everything,
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [short_valid, longer_valid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="high", allow_paid=False)
    assert result.tier_used == "longer"
    assert len(result.content) == 500


@pytest.mark.asyncio
async def test_cascade_invalid_tier_does_not_compete_for_longest() -> None:
    """A tier that fails validation must not become best_result, even if longer."""
    ctx = MagicMock(spec=FetchContext)
    short_valid = Tier(
        "short_valid",
        "free",
        10,
        10000,
        AsyncMock(return_value=RawTierResult(content="x" * 100, status=200)),
        validate=lambda _c: True,
    )
    long_invalid = Tier(
        "long_invalid",
        "free",
        10,
        10000,
        AsyncMock(return_value=RawTierResult(content="y" * 5000, status=200)),
        validate=lambda _c: False,
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [short_valid, long_invalid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="high", allow_paid=False)
    assert result.tier_used == "short_valid"
    assert len(result.content) == 100


@pytest.mark.asyncio
async def test_cascade_tier_log_records_below_floor_with_floor_and_detail() -> None:
    """A passing tier that sits below the floor records error_kind=below_floor + the floor."""
    ctx = MagicMock(spec=FetchContext)
    below = Tier(
        "below",
        "free",
        1500,
        10000,
        AsyncMock(return_value=RawTierResult(content="x" * 1187, status=200)),
        validate=lambda _c: True,
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [below]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert len(result.tier_log) == 1
    entry = result.tier_log[0]
    assert entry.tier == "below"
    assert entry.chars == 1187
    assert entry.floor == 1500
    assert entry.error_kind == "below_floor"
    assert entry.detail and "1187" in entry.detail and "1500" in entry.detail


@pytest.mark.asyncio
async def test_cascade_tier_log_records_http_error_and_preserves_handler_detail() -> None:
    """Handler-supplied RawTierResult.detail flows through to TierLogEntry.detail."""
    ctx = MagicMock(spec=FetchContext)
    http_401 = Tier(
        "jina",
        "free",
        1500,
        10000,
        AsyncMock(return_value=RawTierResult(content="", status=401, detail="jina HTTP 401: nope")),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [http_401]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert len(result.tier_log) == 1
    entry = result.tier_log[0]
    assert entry.status == 401
    assert entry.error_kind == "http_error"
    assert entry.detail == "jina HTTP 401: nope"
    assert entry.duration_ms >= 0


@pytest.mark.asyncio
async def test_cascade_tier_log_records_exception_kind() -> None:
    ctx = MagicMock(spec=FetchContext)
    boom = Tier(
        "explodes",
        "free",
        10,
        100,
        AsyncMock(side_effect=RuntimeError("kaboom")),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [boom]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    entry = result.tier_log[0]
    assert entry.error_kind == "exception"
    assert entry.detail and "RuntimeError" in entry.detail and "kaboom" in entry.detail


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
