"""Tests for the Facebook handler — matches() + 2-tier RapidAPI cascade.

api4 is URL-keyed (primary); scraper3 is pfbid-keyed (fallback). Both
require FETCHER_RAPIDAPI_KEY — handler is STRICT_PAID_TIER so unauthorized
fetches surface as a Problem instead of silently dropping FB URLs to the
article handler.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher.handlers import facebook


_PFBID = "pfbid0AbCdEf123"
_FB_URL = f"https://www.facebook.com/john.doe/posts/{_PFBID}"


def _make_ctx(*, rapidapi_key: str | None = "rapid-key") -> MagicMock:
    ctx = MagicMock()
    ctx.rapidapi_key = rapidapi_key
    ctx.http_client = MagicMock()
    return ctx


# ---------------- matches() ----------------


@pytest.mark.parametrize(
    "url",
    [
        "https://facebook.com/some/post",
        "https://www.facebook.com/some/post",
        "https://m.facebook.com/some/post",
        "https://fb.com/p/abc",
        "https://fb.watch/xyz",
    ],
)
def test_matches_facebook_hosts(url: str) -> None:
    assert facebook.matches(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "https://facebook.example.com/spoof",  # suffix-attack guard
        "https://medium.com/x-abcdef123456",
        "https://www.youtube.com/watch?v=Xyh1EqcjGME",
    ],
)
def test_does_not_match_non_facebook(url: str) -> None:
    assert facebook.matches(url) is False


# ---------------- cascade ----------------


async def test_api4_tier_returns_content_on_success() -> None:
    ctx = _make_ctx()
    with patch(
        "fetcher.handlers.facebook.facebook_api4.fetch_post",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = ("**Author**\n\nbody", "body", "Author")
        result = await facebook._api4_tier(ctx, _FB_URL)

    mock.assert_awaited_once()
    call = mock.await_args
    assert call.kwargs["url"] == _FB_URL
    assert call.kwargs["api_key"] == "rapid-key"
    assert result.status == 200
    assert "body" in result.content
    assert result.metadata == {"title": "body", "author": "Author"}


async def test_api4_tier_surfaces_extractor_error_in_detail() -> None:
    ctx = _make_ctx()
    with patch(
        "fetcher.handlers.facebook.facebook_api4.fetch_post",
        new_callable=AsyncMock,
    ) as mock:
        mock.side_effect = ValueError("RapidAPI facebook-scraper-api4 HTTP 403: forbidden")
        result = await facebook._api4_tier(ctx, _FB_URL)

    assert result.status == 0
    assert result.content == ""
    assert "403" in (result.detail or "")


async def test_api4_tier_skips_without_rapidapi_key() -> None:
    ctx = _make_ctx(rapidapi_key=None)
    with patch("fetcher.handlers.facebook.facebook_api4.fetch_post") as mock:
        result = await facebook._api4_tier(ctx, _FB_URL)

    mock.assert_not_called()
    assert result.status == 0
    assert "RAPIDAPI_KEY not configured" in (result.detail or "")


async def test_scraper3_tier_uses_pfbid_from_url() -> None:
    ctx = _make_ctx()
    with patch(
        "fetcher.handlers.facebook.facebook_scraper3.fetch_post",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = ("**Author**\n\nbody", "body", "Author")
        result = await facebook._scraper3_tier(ctx, _FB_URL)

    call = mock.await_args
    assert call.kwargs["pfbid"] == _PFBID
    assert call.kwargs["api_key"] == "rapid-key"
    assert result.status == 200
    assert "body" in result.content


async def test_scraper3_tier_soft_skips_when_no_pfbid_in_url() -> None:
    """Older /posts/<numeric> URLs don't carry pfbid — scraper3 can't
    handle them. Soft-skip (status=0 + detail) so cascade moves on without
    pretending we tried the upstream call."""
    ctx = _make_ctx()
    url_no_pfbid = "https://www.facebook.com/john.doe/posts/123456"
    with patch("fetcher.handlers.facebook.facebook_scraper3.fetch_post") as mock:
        result = await facebook._scraper3_tier(ctx, url_no_pfbid)

    mock.assert_not_called()
    assert result.status == 0
    assert "pfbid" in (result.detail or "")


def test_handler_is_strict_paid_tier() -> None:
    """Both FB tiers are RapidAPI-only. Without allow_paid the cascade
    yields a Problem, not a silent fall-through to the article handler."""
    assert facebook.STRICT_PAID_TIER is True


def test_handler_registers_two_tiers_in_order() -> None:
    """api4 first (URL-keyed, no pfbid extraction needed), scraper3 second."""
    names = [t.name for t in facebook.TIERS]
    assert names == ["facebook_api4", "facebook_scraper3"]
    for tier in facebook.TIERS:
        assert tier.cost == "paid"
        assert tier.rate_limit_key == "rapidapi"
