"""Tests for the trafilatura extractor's content + metadata extraction."""

from fetcher.extractors import trafilatura as trafilatura_extractor

_HTML = (
    "<html><head>"
    "<title>My Article</title>"
    '<meta name="author" content="Jane Doe">'
    '<meta property="article:published_time" content="2026-03-01T10:00:00Z">'
    "</head><body><article><p>Some real content that is long enough.</p></article></body></html>"
)


def test_extract_metadata_pulls_title_author_date():
    # Trafilatura parses title/author/published from the same HTML it extracts
    # content from — free provenance, no extra network call.
    assert trafilatura_extractor.extract_metadata(_HTML) == {
        "title": "My Article",
        "authors": "Jane Doe",
        "published": "2026-03-01",
    }


def test_extract_metadata_empty_html_is_empty():
    assert trafilatura_extractor.extract_metadata("") == {}
