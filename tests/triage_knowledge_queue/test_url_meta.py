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
