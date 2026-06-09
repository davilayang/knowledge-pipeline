"""Tests for the tavily extractor (Tavily Extract API)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.extractors import tavily


async def test_extract_returns_raw_content() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"results": [{"raw_content": "# md"}]})
    client.post = AsyncMock(return_value=response)

    result = await tavily.extract(client, url="https://example.com", api_key="k")
    assert result == "# md"


async def test_extract_returns_empty_when_no_results() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"results": []})
    client.post = AsyncMock(return_value=response)

    result = await tavily.extract(client, url="https://example.com", api_key="k")
    assert result == ""


async def test_extract_posts_to_api_with_bearer_auth() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"results": [{"raw_content": "x"}]})
    client.post = AsyncMock(return_value=response)

    await tavily.extract(client, url="https://example.com/a", api_key="secret")

    call = client.post.call_args
    assert call.args[0] == "https://api.tavily.com/extract"
    assert call.kwargs["json"] == {"urls": ["https://example.com/a"]}
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret"


async def test_extract_raises_on_http_error() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 401
    response.text = '{"error":"unauthorized"}'
    client.post = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="401"):
        await tavily.extract(client, url="https://example.com", api_key="bad")
