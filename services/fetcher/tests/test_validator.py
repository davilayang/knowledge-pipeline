"""Tests for the content validator (block-page + truncation detection)."""

from pathlib import Path

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


def test_is_acceptable_accepts_body_whose_footer_links_to_another_post() -> None:
    """A footer "Continue reading..." link points at a *different* article — site
    navigation, not this body being cut off.

    Fixture is the real tail of the Jina Reader output for
    seangoedecke.com/they-really-do-think-ai-might-kill-everyone, which on
    2026-09-11 was discarded by all three article tiers because of this link.
    """
    body = (Path(__file__).parent / "fixtures" / "article_footer_nav_link.md").read_text()
    assert is_acceptable(body) is True


def test_is_likely_truncated_detects_login_wall_rendered_as_a_link() -> None:
    """Auth walls are usually a link or button, not prose, so stripping links must
    not let a login-walled body through. That is why "log in" markers match
    everywhere while the navigation-ambiguous ones do not.
    """
    body = "Real article prose. " * 60 + "\n\n[Log in to continue reading](https://site/login)"
    assert is_likely_truncated(body) is True


def test_is_likely_truncated_detects_marker_followed_by_footer_boilerplate() -> None:
    """Nav, tags and legal boilerplate usually follow a cut-off, so the tail scan
    must reach back past them — hence 1000 chars, not the ellipsis check's 200.
    """
    body = "Real article prose. " * 60 + "\n\nContinue reading below.\n\n" + "Footer nav. " * 35
    assert is_likely_truncated(body) is True
