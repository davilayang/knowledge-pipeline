"""Tests for the jina extractor (shared by article + medium handlers)."""

from unittest.mock import AsyncMock, MagicMock

from fetcher.extractors import jina


async def test_fetch_returns_body_and_status() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = "Title: Real Article\n\nbody"
    client.get = AsyncMock(return_value=response)

    body, status = await jina.fetch(client, "https://example.com")
    assert body == "Title: Real Article\n\nbody"
    assert status == 200


async def test_fetch_quotes_url_into_base() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = "ok"
    client.get = AsyncMock(return_value=response)

    await jina.fetch(client, "https://example.com/a b?x=1")
    called_with = client.get.call_args.args[0]
    assert called_with.startswith("https://r.jina.ai/")
    assert "https%3A%2F%2Fexample.com" in called_with


def test_wraps_upstream_error_detects_marker() -> None:
    assert jina.wraps_upstream_error(
        "Warning: Target URL returned error 404: Not Found"
    )
    assert not jina.wraps_upstream_error("Title: Real Article\n\nprose")
    assert not jina.wraps_upstream_error("")
