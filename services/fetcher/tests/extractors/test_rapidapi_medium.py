"""Tests for the RapidAPI Medium paywall-bypass extractor."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.extractors import rapidapi_medium


async def test_fetch_markdown_posts_to_correct_url_with_headers() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"markdown": "# article"})
    client.get = AsyncMock(return_value=response)

    result = await rapidapi_medium.fetch_markdown(
        client, article_id="abc123def456", api_key="secret"
    )

    call = client.get.call_args
    assert call.args[0] == "https://medium2.p.rapidapi.com/article/abc123def456/markdown"
    headers = call.kwargs["headers"]
    assert headers["x-rapidapi-key"] == "secret"
    assert headers["x-rapidapi-host"] == "medium2.p.rapidapi.com"
    assert result == "# article"


async def test_fetch_markdown_returns_empty_when_field_missing() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={})
    client.get = AsyncMock(return_value=response)

    result = await rapidapi_medium.fetch_markdown(client, article_id="x", api_key="k")
    assert result == ""


async def test_fetch_markdown_raises_on_http_error() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 403
    response.text = '{"error":"forbidden"}'
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="403"):
        await rapidapi_medium.fetch_markdown(client, article_id="x", api_key="bad")
