"""Tests for source registry."""

from fetcher.registry import REGISTERED_SOURCES, find_source


def test_registered_sources_order() -> None:
    assert [source.NAME for source in REGISTERED_SOURCES] == ["arxiv", "youtube", "article"]


def test_find_source_routes_specific_before_article() -> None:
    assert find_source("https://arxiv.org/abs/2401.00001").NAME == "arxiv"
    assert find_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ").NAME == "youtube"
    assert find_source("https://example.com/post").NAME == "article"
    assert find_source("mailto:test@example.com") is None
