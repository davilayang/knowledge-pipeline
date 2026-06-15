"""Tests for the RapidAPI facebook-scraper3 fallback extractor.

Pfbid-keyed (`GET /post?post_id=<pfbid>`). Fallback when api4 fails or
quota-exhausted. URL shape detection (`extract_pfbid`) is part of this
module — handler asks "what's the pfbid in this URL?" before invoking.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.extractors.rapidapi import facebook_scraper3


_PFBID = "pfbid0AbCdEf123"
_POST_URL = f"https://www.facebook.com/permalink.php?id=1&v={_PFBID}"


def test_extract_pfbid_finds_token_in_url() -> None:
    assert facebook_scraper3.extract_pfbid(_POST_URL) == _PFBID


def test_extract_pfbid_returns_none_when_absent() -> None:
    """Older /posts/<numeric> and /share/p/<token> URLs don't carry a
    pfbid; caller decides whether that's a skip or an error."""
    assert facebook_scraper3.extract_pfbid("https://www.facebook.com/john/posts/12345") is None


async def test_fetch_post_returns_markdown_title_author() -> None:
    payload = {
        "results": {
            "author": {"name": "Some Author"},
            "message": "Post body line 1.\nLine 2.",
            "timestamp": 1749902400,  # 2026-06-14T10:00:00+00:00
            "url": _POST_URL,
        }
    }
    response = MagicMock()
    response.status_code = 200
    response.headers = {"x-ratelimit-requests-remaining": "10"}
    response.json = MagicMock(return_value=payload)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    markdown, title, author = await facebook_scraper3.fetch_post(
        client, pfbid=_PFBID, api_key="secret"
    )

    call = client.get.call_args
    assert call.args[0] == "https://facebook-scraper3.p.rapidapi.com/post"
    assert call.kwargs["params"] == {"post_id": _PFBID}
    headers = call.kwargs["headers"]
    assert headers["x-rapidapi-key"] == "secret"
    assert headers["x-rapidapi-host"] == "facebook-scraper3.p.rapidapi.com"

    assert "Post body line 1." in markdown
    assert "**Some Author**" in markdown
    assert f"Source: {_POST_URL}" in markdown
    assert title == "Post body line 1."
    assert author == "Some Author"


async def test_fetch_post_raises_on_http_error() -> None:
    response = MagicMock()
    response.status_code = 500
    response.headers = {}
    response.text = "upstream timeout"
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="500"):
        await facebook_scraper3.fetch_post(client, pfbid=_PFBID, api_key="k")


async def test_fetch_post_raises_on_empty_body() -> None:
    payload = {"results": {"author": {"name": "Author"}, "message": "", "url": _POST_URL}}
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.json = MagicMock(return_value=payload)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="empty"):
        await facebook_scraper3.fetch_post(client, pfbid=_PFBID, api_key="k")


async def test_fetch_post_raises_on_unexpected_shape() -> None:
    """No `results` dict → upstream changed the response shape."""
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.json = MagicMock(return_value={"error": "bad request"})
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="unexpected response"):
        await facebook_scraper3.fetch_post(client, pfbid=_PFBID, api_key="k")
