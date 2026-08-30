"""Best-effort URL → page metadata, from one HTTP GET parsed by trafilatura.

`title` / `description` seed Notion's Name and Description; the rest of what
the same parse yields is kept as evidence for later stages. Never raises —
any failure collapses to a UrlMeta holding only redirected_url = input_url.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx
import trafilatura

_TIMEOUT_S = 10.0
_DESCRIPTION_MAX_CHARS = 200

# Default httpx UA gets 403'd by several sites (NYT, etc.) that do basic
# UA-sniffing. Chrome UA unblocks those; sites with real Cloudflare TLS
# fingerprinting (Medium) still 403 — would need curl_cffi to bypass.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class UrlMeta:
    """URL after HTTP redirect resolution + page-level meta. `redirected_url`
    is the post-redirect URL (the URL the browser would land on); distinct
    from `canonical_url` (the normalized identity used for dedup) and from
    `original_url` (the raw input)."""

    redirected_url: str
    title: str | None
    description: str | None
    author: str | None = None
    date: str | None = None
    sitename: str | None = None
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    pagetype: str | None = None


def normalize_iso_day(value: str | None) -> str | None:
    """Any ISO 8601 date or timestamp → its `YYYY-MM-DD` day; anything else →
    None. Publishers and APIs claim many things are dates; dropping the
    unparseable ones keeps a downstream `date.fromisoformat` safe."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).date().isoformat()
    except ValueError:
        return None


def normalize_terms(values: object) -> tuple[str, ...]:
    """Untrusted list of keyword strings → tuple, dropping blanks and
    non-strings. Terms are kept verbatim: splitting a publisher's
    comma-joined keyword string is a guess this layer can't make."""
    if not isinstance(values, list):
        return ()
    return tuple(term for v in values if isinstance(v, str) and (term := v.strip()))


def _normalize(value: str | None, *, max_chars: int | None = None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if max_chars is not None and len(stripped) > max_chars:
        return stripped[:max_chars]
    return stripped


def fetch_url_meta(url: str, *, timeout: float = _TIMEOUT_S) -> UrlMeta:
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=timeout, headers=_HEADERS)
    except httpx.HTTPError:
        return UrlMeta(redirected_url=url, title=None, description=None)

    redirected_url = str(resp.url) or url

    if resp.status_code >= 400:
        return UrlMeta(redirected_url=redirected_url, title=None, description=None)

    content_type = (resp.headers.get("content-type") or "").lower()
    if "html" not in content_type:
        return UrlMeta(redirected_url=redirected_url, title=None, description=None)

    try:
        metadata = trafilatura.extract_metadata(resp.text)
    except Exception:
        return UrlMeta(redirected_url=redirected_url, title=None, description=None)

    title = _normalize(getattr(metadata, "title", None) if metadata else None)
    description = _normalize(
        getattr(metadata, "description", None) if metadata else None,
        max_chars=_DESCRIPTION_MAX_CHARS,
    )
    return UrlMeta(
        redirected_url=redirected_url,
        title=title,
        description=description,
        author=_normalize(getattr(metadata, "author", None) if metadata else None),
        date=normalize_iso_day(getattr(metadata, "date", None) if metadata else None),
        sitename=_normalize(getattr(metadata, "sitename", None) if metadata else None),
        categories=normalize_terms(getattr(metadata, "categories", None) if metadata else None),
        tags=normalize_terms(getattr(metadata, "tags", None) if metadata else None),
        pagetype=_normalize(getattr(metadata, "pagetype", None) if metadata else None),
    )
