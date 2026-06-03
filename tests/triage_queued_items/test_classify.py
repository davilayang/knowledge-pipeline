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


# canonicalize_url — CONTRACT with newsletter-assistant's normalize_url.


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://youtu.be/vy7o1g2iHY8", "https://youtu.be/vy7o1g2iHY8"),
        ("https://youtu.be/vy7o1g2iHY8?si=KNw9IPu3Da2KDR_q", "https://youtu.be/vy7o1g2iHY8"),
        (
            "https://youtube.com/watch?v=BD3vLtWhT5A&si=QawMlhQU1mLmI_IT",
            "https://youtube.com/watch?v=BD3vLtWhT5A",
        ),
        (
            "https://www.youtube.com/watch?v=F8X9_Dp3ZUk",
            "https://www.youtube.com/watch?v=F8X9_Dp3ZUk",
        ),
        (
            "https://youtube.com/watch?v=abc123&utm_source=x",
            "https://youtube.com/watch?v=abc123",
        ),
        ("https://m.youtube.com/watch?v=abc123&si=Y", "https://m.youtube.com/watch?v=abc123"),
        (
            "https://music.youtube.com/watch?v=abc123&list=PL",
            "https://music.youtube.com/watch?v=abc123",
        ),
        ("https://example.com/post?utm_source=newsletter&id=42", "https://example.com/post"),
        ("https://example.com/?fbclid=xxx&keep=yes", "https://example.com"),
        (
            "https://medium.com/data-science-collective/ds-star-1c1a7b593277?source=home_for_you",
            "https://medium.com/data-science-collective/ds-star-1c1a7b593277",
        ),
        ("https://example.com/post#section-2", "https://example.com/post"),
        ("https://arxiv.org/abs/2305.14283", "https://arxiv.org/abs/2305.14283"),
    ],
)
def test_canonicalize_matches_na_normalize_url(raw: str, expected: str):
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
