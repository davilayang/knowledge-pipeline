"""Best-effort URL → page metadata for triage display and downstream evidence.

One HTTP GET, parsed once by trafilatura. `title` / `description` seed Notion's
Name (if the user left it blank) and Description; `author` / `date` / `sitename`
/ `categories` / `tags` / `pagetype` are the rest of what that same parse
yields, kept as attribution and shape evidence for later stages rather than
discarded. Never raises — network errors, non-HTML responses, or missing tags
collapse to a UrlMeta holding only redirected_url = input_url. Triage must not
fail on enrichment.
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
    None. Callers hand this whatever a publisher or an API claims is a date,
    so an unparseable value is dropped rather than stored: a downstream
    `date.fromisoformat` must never trip on it."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).date().isoformat()
    except ValueError:
        return None


def normalize_terms(values: object) -> tuple[str, ...]:
    """A list of keyword / section strings → tuple, dropping blanks and any
    non-string entry. Callers pass whatever trafilatura parsed off a page or
    whatever an older build wrote into `enrichment_json`, so neither the type
    nor the contents are trusted. Terms are otherwise kept verbatim —
    splitting a publisher's comma-joined keyword string is a guess this layer
    has no basis for."""
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
