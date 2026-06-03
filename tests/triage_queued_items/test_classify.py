import pytest
from orchestrators.defs.triage_queued_items.classify import (
    ALL_CONTENT_TYPES,
    canonicalize_url,
    classify_content_type,
    is_tier_a,
)

# ---------------------------------------------------------------------------
# classify_content_type
# ---------------------------------------------------------------------------


def test_youtube_canonical_url():
    assert classify_content_type("https://youtube.com/watch?v=abc123") == "YouTube"


def test_youtube_short_url():
    assert classify_content_type("https://youtu.be/abc123") == "YouTube"


def test_youtube_mobile_url():
    assert classify_content_type("https://m.youtube.com/watch?v=abc123") == "YouTube"


def test_youtube_music_url():
    assert classify_content_type("https://music.youtube.com/watch?v=abc123") == "YouTube"


def test_youtube_shorts_url():
    assert classify_content_type("https://youtube.com/shorts/abc123") == "YouTube"


def test_arxiv_abs_url():
    assert classify_content_type("https://arxiv.org/abs/2310.06770") == "arXiv"


def test_arxiv_pdf_url():
    assert classify_content_type("https://arxiv.org/pdf/2310.06770.pdf") == "arXiv"


def test_arxiv_subdomain_url():
    assert classify_content_type("https://export.arxiv.org/abs/2310.06770") == "arXiv"


def test_arbitrary_blog_url():
    assert classify_content_type("https://blog.example.com/post") == "Article"


def test_substack_url():
    assert classify_content_type("https://someone.substack.com/p/post-title") == "Article"


def test_pdf_falls_through_to_article():
    assert classify_content_type("https://example.com/whitepaper.pdf") == "Article"


def test_apple_podcast_falls_through_to_article():
    assert classify_content_type("https://podcasts.apple.com/us/podcast/foo/id123") == "Article"


def test_classification_returns_value_in_all_content_types_set():
    urls = [
        "https://youtube.com/watch?v=abc123",
        "https://arxiv.org/abs/2310.06770",
        "https://blog.example.com/post",
        "https://example.com/whitepaper.pdf",
        "https://podcasts.apple.com/us/podcast/foo/id123",
    ]
    for url in urls:
        assert classify_content_type(url) in ALL_CONTENT_TYPES


# ---------------------------------------------------------------------------
# canonicalize_url — CONTRACT with newsletter-assistant's normalize_url.
#
# These outputs must equal what NA's normalize_url produces (NA's
# packages/knowledge/src/knowledge/fetcher/orchestrator.py). NA's
# kp_queue_cache tier does WHERE canonical_url = ? against kp's queue.db,
# where ? is NA's normalised form. Drift = silent cache miss → NA falls
# through to slower live fetchers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # youtu.be: keep the youtu.be host (do NOT rewrite to youtube.com/watch?v=)
        ("https://youtu.be/vy7o1g2iHY8", "https://youtu.be/vy7o1g2iHY8"),
        # youtu.be with ?si= tracking: drop the query string entirely
        ("https://youtu.be/vy7o1g2iHY8?si=KNw9IPu3Da2KDR_q", "https://youtu.be/vy7o1g2iHY8"),
        # youtube.com with &si= tracking: keep only v=
        (
            "https://youtube.com/watch?v=BD3vLtWhT5A&si=QawMlhQU1mLmI_IT",
            "https://youtube.com/watch?v=BD3vLtWhT5A",
        ),
        # www.youtube.com: preserve www in the output (NA does not strip)
        (
            "https://www.youtube.com/watch?v=F8X9_Dp3ZUk",
            "https://www.youtube.com/watch?v=F8X9_Dp3ZUk",
        ),
        # youtube.com with utm_source: keep only v=
        (
            "https://youtube.com/watch?v=abc123&utm_source=x",
            "https://youtube.com/watch?v=abc123",
        ),
        # m.youtube.com: same treatment as youtube.com
        (
            "https://m.youtube.com/watch?v=abc123&si=Y",
            "https://m.youtube.com/watch?v=abc123",
        ),
        # music.youtube.com: same treatment
        (
            "https://music.youtube.com/watch?v=abc123&list=PL",
            "https://music.youtube.com/watch?v=abc123",
        ),
        # Non-YouTube host: strip entire query + fragment (NA's default)
        (
            "https://example.com/post?utm_source=newsletter&id=42",
            "https://example.com/post",
        ),
        (
            "https://example.com/?fbclid=xxx&keep=yes",
            "https://example.com",
        ),
        # Medium tracking source params: stripped under the strict default
        (
            "https://medium.com/data-science-collective/ds-star-1c1a7b593277?source=home_for_you",
            "https://medium.com/data-science-collective/ds-star-1c1a7b593277",
        ),
        # Fragment dropped
        (
            "https://example.com/post#section-2",
            "https://example.com/post",
        ),
        # arXiv abs: passes through (already canonical)
        ("https://arxiv.org/abs/2305.14283", "https://arxiv.org/abs/2305.14283"),
    ],
)
def test_canonicalize_matches_na_normalize_url(raw: str, expected: str):
    """canonical_url must equal NA's normalize_url output.

    See ai-plannings/2026-06-03_align-canonical-url-with-na-normalize.md
    for the kp_queue_cache miss regression that motivated this contract.
    """
    assert canonicalize_url(raw) == expected


# ---------------------------------------------------------------------------
# is_tier_a
# ---------------------------------------------------------------------------


def test_is_tier_a_youtube():
    assert is_tier_a("YouTube") is True


def test_is_tier_a_arxiv():
    assert is_tier_a("arXiv") is True


def test_is_tier_a_article():
    assert is_tier_a("Article") is False


def test_is_tier_a_other():
    assert is_tier_a("Other") is False
