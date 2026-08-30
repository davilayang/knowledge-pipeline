"""Tests for the lightweight URL → (redirected_url, title, description) fetcher.

The fetcher is best-effort: any network or parse error returns an empty
UrlMeta with redirected_url = input_url and title/description = None. Triage
must not fail on a fetch error.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from orchestrators.defs.triage_knowledge_queue.url_meta import UrlMeta, fetch_url_meta


def _fake_response(
    *,
    url: str,
    text: str = "",
    content_type: str = "text/html; charset=utf-8",
    status_code: int = 200,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.url = httpx.URL(url)
    resp.text = text
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    return resp


def _patch_get(resp: MagicMock | None = None, *, exc: Exception | None = None):
    fake = MagicMock()
    if exc is not None:
        fake.side_effect = exc
    else:
        fake.return_value = resp
    return patch("orchestrators.defs.triage_knowledge_queue.url_meta.httpx.get", fake)


_HTML_WITH_META = """
<html>
<head>
  <title>  Hello World  </title>
  <meta name="description" content="A short description of the post.">
</head>
<body><p>Body text.</p></body>
</html>
"""

_HTML_WITH_OG_DESCRIPTION = """
<html>
<head>
  <title>OG Page</title>
  <meta property="og:description" content="OG-only description">
</head>
<body></body>
</html>
"""


def test_returns_title_and_description_from_html():
    resp = _fake_response(url="https://example.com/post", text=_HTML_WITH_META)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/post")
    assert meta.redirected_url == "https://example.com/post"
    assert meta.title == "Hello World"
    assert meta.description == "A short description of the post."


def test_falls_back_to_og_description_when_meta_description_missing():
    resp = _fake_response(url="https://example.com/og", text=_HTML_WITH_OG_DESCRIPTION)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/og")
    assert meta.description == "OG-only description"


def test_returns_redirected_url_after_redirect():
    """httpx exposes the post-redirect URL via resp.url when follow_redirects=True."""
    resp = _fake_response(
        url="https://example.com/final?utm_source=newsletter",
        text=_HTML_WITH_META,
    )
    with _patch_get(resp):
        meta = fetch_url_meta("https://t.co/abc123")
    assert meta.redirected_url == "https://example.com/final?utm_source=newsletter"


def test_returns_empty_meta_on_non_html_response():
    """PDF / image / other non-HTML content types: skip parsing, no title/description."""
    resp = _fake_response(
        url="https://example.com/paper.pdf",
        text="%PDF-1.4\n...binary garbage...",
        content_type="application/pdf",
    )
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/paper.pdf")
    assert meta.redirected_url == "https://example.com/paper.pdf"
    assert meta.title is None
    assert meta.description is None


def test_returns_empty_meta_on_network_error():
    """Any httpx exception → empty meta, redirected_url = input. Does NOT raise."""
    with _patch_get(exc=httpx.ConnectError("connection refused")):
        meta = fetch_url_meta("https://broken.example.com")
    assert meta.redirected_url == "https://broken.example.com"
    assert meta.title is None
    assert meta.description is None


def test_returns_empty_meta_on_non_2xx_status():
    """5xx / 4xx → no parsing; redirected_url still captured if available."""
    resp = _fake_response(
        url="https://example.com/missing",
        text="<html></html>",
        status_code=404,
    )
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/missing")
    assert meta.title is None
    assert meta.description is None


def test_strips_whitespace_and_newlines_from_title():
    html = "<html><head><title>\n\n  Trimmed  \n</title></head></html>"
    resp = _fake_response(url="https://example.com", text=html)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com")
    assert meta.title == "Trimmed"


def test_truncates_long_description():
    long_desc = "x" * 500
    html = (
        f'<html><head><title>t</title><meta name="description" content="{long_desc}"></head></html>'
    )
    resp = _fake_response(url="https://example.com", text=html)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com")
    assert meta.description is not None
    assert len(meta.description) <= 200


def test_empty_title_normalized_to_none():
    """trafilatura sometimes returns "" — treat as no title."""
    html = "<html><head><title>   </title></head></html>"
    resp = _fake_response(url="https://example.com", text=html)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com")
    assert meta.title is None


def test_returns_immutable_dataclass():
    meta = UrlMeta(redirected_url="x", title=None, description=None)
    with pytest.raises(Exception):
        meta.redirected_url = "y"  # type: ignore[misc]


_HTML_WITH_ATTRIBUTION = """
<html>
<head>
  <title>The Rise of Multi-Query Engines</title>
  <meta name="description" content="How AI opens up more options for querying.">
  <meta name="author" content="Hugo Lu">
  <meta property="og:site_name" content="Orchestra Newsletter">
  <meta property="article:published_time" content="2026-05-28T09:00:00Z">
  <meta name="keywords" content="data, orchestration">
  <meta property="og:type" content="article">
</head>
<body><article><p>Body text long enough for the extractor to accept it.</p></article></body>
</html>
"""


def test_keeps_author_and_sitename_from_html_metadata():
    """trafilatura already parses these; triage used to discard them. `author`
    is publisher-supplied HTML metadata (org accounts, editorial bylines,
    syndication), so it is evidence about attribution, not verified authorship."""
    resp = _fake_response(url="https://example.com/post", text=_HTML_WITH_ATTRIBUTION)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/post")
    assert meta.author == "Hugo Lu"
    assert meta.sitename == "Orchestra Newsletter"


def test_keeps_publication_date_as_iso_day():
    """trafilatura's htmldate backend normally emits YYYY-MM-DD; the field is
    stored as a plain day so a consumer can parse it with date.fromisoformat."""
    resp = _fake_response(url="https://example.com/post", text=_HTML_WITH_ATTRIBUTION)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/post")
    assert meta.date == "2026-05-28"


def test_drops_unparseable_publication_date():
    """trafilatura permits custom output date formats, so the value is not
    assumed to be ISO — anything date.fromisoformat could not read is dropped
    rather than handed downstream."""
    metadata = MagicMock(title="t", description=None, author=None, sitename=None)
    metadata.date = "28 May 2026"
    resp = _fake_response(url="https://example.com/post", text=_HTML_WITH_ATTRIBUTION)
    with (
        _patch_get(resp),
        patch(
            "orchestrators.defs.triage_knowledge_queue.url_meta.trafilatura.extract_metadata",
            return_value=metadata,
        ),
    ):
        meta = fetch_url_meta("https://example.com/post")
    assert meta.date is None


def test_keeps_classification_hints_from_html_metadata():
    """`pagetype` (og:type) cross-checks how the piece is shaped; keywords /
    section metadata are topic hints. All three are parsed by trafilatura on
    the call triage already makes, so keeping them costs no extra request."""
    resp = _fake_response(url="https://example.com/post", text=_HTML_WITH_ATTRIBUTION)
    with _patch_get(resp):
        meta = fetch_url_meta("https://example.com/post")
    assert meta.pagetype == "article"
    assert meta.tags == ("data, orchestration",)
    assert meta.categories == ()
