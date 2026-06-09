"""Tests for handler registry."""

from fetcher.registry import REGISTERED_HANDLERS, find_handler


def test_registered_handlers_order() -> None:
    assert [handler.NAME for handler in REGISTERED_HANDLERS] == ["arxiv", "youtube", "article"]


def test_find_handler_routes_specific_before_article() -> None:
    assert find_handler("https://arxiv.org/abs/2401.00001").NAME == "arxiv"
    assert find_handler("https://www.youtube.com/watch?v=dQw4w9WgXcQ").NAME == "youtube"
    assert find_handler("https://example.com/post").NAME == "article"
    assert find_handler("mailto:test@example.com") is None
