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
    assert jina.wraps_upstream_error("Warning: Target URL returned error 404: Not Found")
    assert not jina.wraps_upstream_error("Title: Real Article\n\nprose")
    assert not jina.wraps_upstream_error("")


def test_strip_preamble_removes_full_preamble() -> None:
    body = (
        "Title: My Article\n"
        "URL Source: https://example.com/a\n"
        "Published Time: 2026-06-29T00:00:00Z\n\n"
        "Markdown Content:\n"
        "# My Article\n\nThe real body."
    )
    assert jina.strip_preamble(body) == "# My Article\n\nThe real body."


def test_strip_preamble_handles_varied_fields_and_order() -> None:
    # Missing Published Time, extra Image / Language fields — still anchored on the marker.
    body = (
        "Title: My Article\n"
        "URL Source: https://example.com/a\n"
        "Image: https://example.com/cover.png\n"
        "Language: en\n\n"
        "Markdown Content:\n"
        "Real body starts here."
    )
    assert jina.strip_preamble(body) == "Real body starts here."


def test_strip_preamble_noop_without_marker() -> None:
    # Starts like a preamble but has no Markdown Content marker — leave unchanged.
    body = "Title: Odd\nURL Source: https://example.com/a\nno marker here"
    assert jina.strip_preamble(body) == body


def test_strip_preamble_noop_for_non_jina_content() -> None:
    # trafilatura / Tavily / RapidAPI output has no Jina preamble — pass through.
    traf = "# Real Article\n\nProse extracted by trafilatura, no preamble."
    assert jina.strip_preamble(traf) == traf
    # Even if the marker phrase appears inline, the missing Title: lead-in guards it.
    inline = "Some prose that mentions Markdown Content: in passing."
    assert jina.strip_preamble(inline) == inline
