"""Tests for the RapidAPI facebook-scraper-api4 extractor.

URL-keyed Facebook post fetcher. Primary FB tier. Response shape is a list
with one object carrying `details.post_link` + `values.text`/`publish_time`
+ `values.shared_post_details.name`. We render that into NA-compatible
markdown and surface (title, author) alongside.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.extractors.rapidapi import facebook_api4


_POST_URL = "https://www.facebook.com/some/post/pfbid0abc"


def _ok_response(payload: object) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"x-ratelimit-requests-remaining": "42"}
    response.json = MagicMock(return_value=payload)
    return response


async def test_fetch_post_returns_markdown_title_author_for_200_payload() -> None:
    """Happy path: API returns a list with one post dict — extractor
    renders body + author/time header, surfaces first-line title + author."""
    payload = [
        {
            "details": {"post_link": _POST_URL},
            "values": {
                "text": "First line of post.\nMore body content here.",
                "publish_time": "2026-06-14T10:00:00Z",
                "shared_post_details": {"name": "Some Author"},
            },
        }
    ]
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response(payload))

    markdown, title, author, published = await facebook_api4.fetch_post(
        client, url=_POST_URL, api_key="secret"
    )

    call = client.get.call_args
    assert call.args[0] == "https://facebook-scraper-api4.p.rapidapi.com/get_facebook_post_details"
    assert call.kwargs["params"] == {"link": _POST_URL}
    headers = call.kwargs["headers"]
    assert headers["x-rapidapi-key"] == "secret"
    assert headers["x-rapidapi-host"] == "facebook-scraper-api4.p.rapidapi.com"

    assert "First line of post." in markdown
    assert "**Some Author**" in markdown
    assert "_2026-06-14T10:00:00Z_" in markdown
    assert f"Source: {_POST_URL}" in markdown
    assert title == "First line of post."
    assert author == "Some Author"
    assert published == "2026-06-14T10:00:00Z"  # raw; build_metadata normalizes it


async def test_fetch_post_raises_on_http_error() -> None:
    response = MagicMock()
    response.status_code = 403
    response.headers = {}
    response.text = '{"error":"forbidden"}'
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="403"):
        await facebook_api4.fetch_post(client, url=_POST_URL, api_key="bad")


async def test_fetch_post_raises_on_quota_exhausted() -> None:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"x-ratelimit-requests-remaining": "0"}
    response.json = MagicMock(return_value=[])
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="quota exhausted"):
        await facebook_api4.fetch_post(client, url=_POST_URL, api_key="k")


async def test_fetch_post_raises_on_empty_body() -> None:
    """Post fetched, but no `text` field — surfaces as ValueError so the
    handler turns it into a tier_log `detail` instead of a successful
    empty-string content."""
    payload = [
        {
            "details": {"post_link": _POST_URL},
            "values": {"text": "", "shared_post_details": {"name": "Author"}},
        }
    ]
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response(payload))

    with pytest.raises(ValueError, match="empty"):
        await facebook_api4.fetch_post(client, url=_POST_URL, api_key="k")
