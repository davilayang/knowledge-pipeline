import pytest
from orchestrators.defs.triage_queued_items.classify import (
    ALL_CONTENT_TYPES,
    canonicalize_url,
    classify_content_type,
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


@pytest.mark.parametrize(
    "url",
    [
        "https://www.podtrac.com/pts/redirect.mp3/chrt.fm/track/E581B9/foo.mp3",
        "https://traffic.libsyn.com/show/episode.MP3",
        "https://example.com/episode.m4a",
        "https://example.com/episode.ogg",
        "https://example.com/episode.wav",
        "https://example.com/episode.opus",
    ],
)
def test_audio_suffix_classifies_as_podcast(url: str):
    assert classify_content_type(url) == "Podcast"


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
        # arXiv: pdf, html, bare-ID, versioned, and host variants all collapse to /abs/<id>.
        ("https://arxiv.org/pdf/2305.14283v2.pdf", "https://arxiv.org/abs/2305.14283"),
        ("https://arxiv.org/html/2606.09498v1", "https://arxiv.org/abs/2606.09498"),
        ("https://www.arxiv.org/abs/2305.14283v3", "https://www.arxiv.org/abs/2305.14283"),
        ("https://export.arxiv.org/pdf/2305.14283", "https://export.arxiv.org/abs/2305.14283"),
        ("https://arxiv.org/abs/2305.14283?utm=x#sec", "https://arxiv.org/abs/2305.14283"),
    ],
)
def test_canonicalize_matches_na_normalize_url(raw: str, expected: str):
    assert canonicalize_url(raw) == expected
