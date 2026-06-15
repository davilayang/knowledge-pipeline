"""Tests for handler registry."""

from fetcher.registry import REGISTERED_HANDLERS, find_handler


def test_registered_handlers_order() -> None:
    assert [handler.NAME for handler in REGISTERED_HANDLERS] == [
        "arxiv",
        "youtube",
        "medium",
        "facebook",
        "pdf",
        "podcast",
        "article",
    ]


def test_find_handler_routes_specific_before_article() -> None:
    assert find_handler("https://arxiv.org/abs/2401.00001").NAME == "arxiv"
    assert find_handler("https://www.youtube.com/watch?v=dQw4w9WgXcQ").NAME == "youtube"
    assert find_handler("https://example.com/post").NAME == "article"
    assert find_handler("mailto:test@example.com") is None


def test_find_handler_routes_pdf_url_to_pdf_not_article() -> None:
    assert find_handler("https://example.com/paper.pdf").NAME == "pdf"
    # arXiv PDFs still go to the arxiv handler (more-specific match wins via order).
    assert find_handler("https://arxiv.org/pdf/2401.00001v2.pdf").NAME == "arxiv"


def test_find_handler_routes_facebook_url_to_facebook_not_article() -> None:
    """FB URLs without `allow_paid=true` would otherwise fall through to
    the article handler and return YouTube-style 'login required' junk.
    Routing to the facebook handler surfaces a Problem instead."""
    assert find_handler("https://www.facebook.com/john.doe/posts/pfbid0abc").NAME == "facebook"
    assert find_handler("https://fb.watch/abcxyz").NAME == "facebook"
    # Spoof: facebook.example.com is NOT a FB host — must route as article.
    assert find_handler("https://facebook.example.com/about").NAME == "article"


def test_find_handler_routes_medium_url_to_medium_not_article() -> None:
    from fetcher.handlers import medium

    # Pin the domain set for deterministic routing.
    original = medium._MEDIUM_DOMAINS
    medium._MEDIUM_DOMAINS = {"medium.com", "towardsdatascience.com"}
    try:
        assert find_handler("https://towardsdatascience.com/title-abc123def456").NAME == "medium"
    finally:
        medium._MEDIUM_DOMAINS = original
