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


async def test_cascade_best_result_keeps_winning_tier_metadata() -> None:
    # No tier meets floor → the LONGEST validated tier wins on content. Its
    # provenance must win too — a later, shorter validated tier must NOT stamp its
    # own date onto the winner's content (they'd be from different fetches).
    ctx = MagicMock(spec=FetchContext)
    longest = Tier(
        "longest",
        "free",
        10**9,  # nothing meets this floor
        10**9,
        AsyncMock(
            return_value=RawTierResult(
                content="y" * 500, status=200, metadata={"title": "A", "published": "2026-01-01"}
            )
        ),
    )
    shorter_later = Tier(
        "shorter",
        "free",
        10**9,
        10**9,
        AsyncMock(
            return_value=RawTierResult(
                content="x" * 100, status=200, metadata={"title": "B", "published": "2026-09-09"}
            )
        ),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [longest, shorter_later]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert result.tier_used == "longest"
    assert result.content == "y" * 500
    assert result.metadata == {"title": "A", "published": "2026-01-01"}


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
async def test_cascade_carries_metadata_from_validation_failed_tier() -> None:
    """A tier that OPTS IN still contributes its preamble date when its body fails.

    Paywalled Medium/FB: Jina extracts a real `Published Time:` into metadata but
    its body is a paywall stub that fails `validate`. Jina's preamble is
    structured (independent of body quality), so its tier sets
    `carry_meta_on_reject=True` and the date carries onto the winning tier — the
    invalid content itself must NOT win, only its metadata survives.
    """
    ctx = MagicMock(spec=FetchContext)
    stub = Tier(
        "jina_stub",
        "free",
        10,
        100,
        AsyncMock(
            return_value=RawTierResult(
                content="paywall stub",
                status=200,
                metadata={"published": "2026-03-01"},
            )
        ),
        validate=lambda _c: False,  # body rejected
        carry_meta_on_reject=True,  # preamble date is trustworthy anyway
    )
    paid = Tier(
        "rapidapi",
        "paid",
        10,
        10,
        AsyncMock(return_value=RawTierResult(content="full body text", status=200, metadata={})),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [stub, paid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=True)
    assert result.tier_used == "rapidapi"
    assert result.content == "full body text"
    assert result.metadata == {"published": "2026-03-01"}


@pytest.mark.asyncio
async def test_cascade_does_not_carry_metadata_from_rejected_tier_without_optin() -> None:
    """A rejected tier that did NOT opt in must not stamp its metadata onto the winner.

    article.py's trafilatura tier scrapes title/date heuristically off the same
    HTML that failed body validation — a soft-404 / login wall can yield a
    plausible-but-WRONG date. Without `carry_meta_on_reject`, that date must be
    discarded, not carried onto a genuinely-good winner (a wrong provenance date
    is worse than a missing one). Covers the best_result fallback path.
    """
    ctx = MagicMock(spec=FetchContext)
    good = Tier(
        "jina_good",
        "free",
        10**9,  # validated but below floor → becomes best_result winner, no date
        10**9,
        AsyncMock(return_value=RawTierResult(content="y" * 500, status=200, metadata={})),
        validate=lambda _c: True,
    )
    trafilatura = Tier(
        "trafilatura",
        "free",
        10,
        100,
        AsyncMock(
            return_value=RawTierResult(
                content="soft-404 boilerplate",
                status=200,
                metadata={"published": "1999-01-01"},  # bogus scrape off a broken page
            )
        ),
        validate=lambda _c: False,  # body rejected
        # no carry_meta_on_reject → its metadata must NOT survive
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [good, trafilatura]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert result.tier_used == "jina_good"
    assert result.metadata == {}  # the bogus 1999 date was discarded, not carried


@pytest.mark.asyncio
async def test_cascade_honours_handler_tier_order_paid_first() -> None:
    """A handler that lists its paid quality tier first tries it before the cheap one.

    arXiv wants llamaparse (quality) ahead of pymupdf (cheap fallback). The
    cascade must walk TIERS in the handler's declared order, not force every
    free tier ahead of every paid one.
    """
    ctx = MagicMock(spec=FetchContext)
    paid_first = Tier(
        "paid_first",
        "paid",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="p" * 50, status=200)),
    )
    free_fallback = Tier(
        "free_fallback",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="f" * 50, status=200)),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [paid_first, free_fallback]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=True)
    assert result.tier_used == "paid_first"
    free_fallback.run.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_skips_paid_tier_when_not_allowed_paid_first() -> None:
    """When allow_paid=False, a paid-first handler still falls back to its free tier."""
    ctx = MagicMock(spec=FetchContext)
    paid_first = Tier(
        "paid_first",
        "paid",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="p" * 50, status=200)),
    )
    free_fallback = Tier(
        "free_fallback",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="f" * 50, status=200)),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = False
        TIERS = [paid_first, free_fallback]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert result.tier_used == "free_fallback"
    paid_first.run.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_strict_paid_tier_skipped_when_not_allowed_does_not_raise() -> None:
    """STRICT_PAID_TIER must not fire on a paid tier that allow_paid=False skips.

    The cost gate skips the paid tier before its run/except is reached, so a
    strict handler with allow_paid=False falls back to the free tier rather than
    raising — the paid tier never executes.
    """
    ctx = MagicMock(spec=FetchContext)
    free = Tier(
        "free",
        "free",
        10,
        100,
        AsyncMock(return_value=RawTierResult(content="x" * 50, status=200)),
    )
    paid = Tier(
        "paid",
        "paid",
        10,
        10,
        AsyncMock(side_effect=ValueError("must never run")),
    )

    class FakeSource:
        NAME = "fake"
        STRICT_PAID_TIER = True
        TIERS = [free, paid]

        @staticmethod
        def matches(url: str) -> bool:
            return True

    result = await run_cascade(FakeSource, ctx, "https://x", quality="fast", allow_paid=False)
    assert result.tier_used == "free"
    paid.run.assert_not_called()


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
