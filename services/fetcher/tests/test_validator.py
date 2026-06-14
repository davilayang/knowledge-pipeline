"""Tests for the content validator (block-page + truncation detection)."""

from fetcher.validator import (
    MIN_CONTENT_CHARS,
    is_acceptable,
    is_likely_truncated,
    is_valid_content,
)


def test_is_valid_content_rejects_short_bodies() -> None:
    assert is_valid_content("x" * (MIN_CONTENT_CHARS - 1)) is False
    assert is_valid_content("x" * MIN_CONTENT_CHARS) is True


def test_is_valid_content_rejects_javascript_wall() -> None:
    body = "x" * 2000 + "\n\nPlease enable JavaScript and cookies to continue.\n"
    assert is_valid_content(body) is False


def test_is_valid_content_rejects_cloudflare_challenge() -> None:
    body = "x" * 2000 + "\n\nChecking if the site connection is secure\nCloudflare Ray ID: abc\n"
    assert is_valid_content(body) is False


def test_is_valid_content_rejects_medium_paywall() -> None:
    body = "x" * 2000 + "\n\nThis story is only available to Medium members.\n"
    assert is_valid_content(body) is False


def test_is_valid_content_accepts_real_article() -> None:
    body = "# Real Article\n\n" + ("This is genuine prose. " * 80) + "\n\nFinal thought."
    assert is_valid_content(body) is True


def test_is_likely_truncated_detects_see_more_marker() -> None:
    body = "Lots of real content. " * 60 + "\n\nLog in to continue reading."
    assert is_likely_truncated(body) is True


def test_is_likely_truncated_detects_ellipsis_tail() -> None:
    body = "Real content prose. " * 60 + "And then suddenly the story ends mid-sent..."
    assert is_likely_truncated(body) is True


def test_is_likely_truncated_detects_long_body_without_terminal_punct() -> None:
    # 600 chars, last 200 contain no period/!/?
    body = "Some intro prose. " + "x" * 580
    assert is_likely_truncated(body) is True


def test_is_likely_truncated_accepts_normal_article_end() -> None:
    body = "Real content. " * 60 + "\n\nA proper conclusion to the article."
    assert is_likely_truncated(body) is False


def test_is_likely_truncated_skips_short_bodies() -> None:
    # Short bodies (image captions, hot takes) legitimately have no terminal punct.
    body = "image caption here no period"
    assert is_likely_truncated(body) is False


def test_is_acceptable_requires_both_valid_and_not_truncated() -> None:
    """The AND-logic of is_acceptable: only valid AND not-truncated returns True.
    Invalid-content + too-short cases are already covered by is_valid_content
    tests above — replicating them here adds no coverage."""
    valid_complete = "Real article body. " * 60 + "\n\nProper conclusion."
    assert is_acceptable(valid_complete) is True

    valid_but_truncated = "Real article. " * 60 + "Read the full article on Medium."
    assert is_acceptable(valid_but_truncated) is False
