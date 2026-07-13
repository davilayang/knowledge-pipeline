"""URL classification + canonicalization. Pure-Python; no I/O.

`classify_content_type` emits the kp `Content Type` taxonomy — the lowercase
routing names shared with the fetcher via `domains.classify_url_type`, so a URL's
type and the handler that fetches it can't disagree. These get written back to
Notion as `Content Type` select-property values, so spelling must match the
Notion SELECT options exactly.
"""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from domains.arxiv_urls import extract_arxiv_id
from domains.content_urls import classify_url_type

CONTENT_TYPE_ARTICLE = "article"
CONTENT_TYPE_YOUTUBE = "youtube"
CONTENT_TYPE_ARXIV = "arxiv"
CONTENT_TYPE_MEDIUM = "medium"
CONTENT_TYPE_FACEBOOK = "facebook"
CONTENT_TYPE_GITHUB = "github"
CONTENT_TYPE_FILE_PDF = "file_pdf"
CONTENT_TYPE_FILE_AUDIO = "file_audio"
# `other` is never auto-emitted (article is the catch-all) — it's a user-only
# override value, kept as a valid Notion Content Type option.
CONTENT_TYPE_OTHER = "other"

ALL_CONTENT_TYPES = {
    CONTENT_TYPE_YOUTUBE,
    CONTENT_TYPE_ARXIV,
    CONTENT_TYPE_MEDIUM,
    CONTENT_TYPE_FACEBOOK,
    CONTENT_TYPE_GITHUB,
    CONTENT_TYPE_FILE_PDF,
    CONTENT_TYPE_FILE_AUDIO,
    CONTENT_TYPE_ARTICLE,
    CONTENT_TYPE_OTHER,
}

# Web-page types that share `article`'s HTML-meta enrichment + display seeding
# (Medium/Facebook/GitHub were all `Article` before the taxonomy split).
ARTICLE_LIKE_TYPES = (
    CONTENT_TYPE_ARTICLE,
    CONTENT_TYPE_MEDIUM,
    CONTENT_TYPE_FACEBOOK,
    CONTENT_TYPE_GITHUB,
)


def classify_content_type(url: str) -> str:
    """Pure URL → kp Content Type, delegated to the shared `classify_url_type`.

    Returns the lowercase taxonomy: youtube / arxiv / medium / facebook / github /
    file_pdf / file_audio / article (the catch-all). The fetcher routes on the same
    function, so content_type and fetch handler always agree (e.g. a non-paper
    arxiv.org page is `article`, matching that it's fetched by the article handler).
    """
    return classify_url_type(url)


def normalize_url(url: str) -> str:
    """Pure URL normalization — must equal newsletter-assistant's
    `normalize_url` (`packages/knowledge/src/knowledge/fetcher/orchestrator.py`)
    byte-for-byte. NA's `kp_queue_cache` looks up by this exact string;
    drift = silent miss. Name kept identical across both repos so the
    contract is self-documenting.

    Distinct from the fetcher service's `canonicalize()` — that one is a
    network-bound HEAD-follow used as an internal cache key. This function
    is pure (no I/O), deterministic, and produces the cross-repo identifier.

    arXiv: any of /abs, /pdf, /html, or bare-ID forms collapse to
    `<scheme>://<netloc>/abs/<id>` (version suffix dropped). Mirrors NA's
    abs/pdf/bare behavior; the html/ surface is a kp extension — NA's
    `is_arxiv_url` does not yet recognise html/, so html-form lookups
    via NA continue to fall through to the generic path until NA
    catches up. New html ingestion paths reach kp via direct Notion
    paste, not NA, so the temporary drift is one-sided."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").removeprefix("www.")

    if hostname in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        v = parse_qs(parsed.query).get("v", [""])[0]
        query = urlencode({"v": v}) if v else ""
        return urlunparse(parsed._replace(query=query, fragment="")).rstrip("/")

    arxiv_id = extract_arxiv_id(url)
    if arxiv_id is not None:
        return urlunparse(
            parsed._replace(
                netloc=(parsed.netloc or "").lower(),
                path=f"/abs/{arxiv_id}",
                query="",
                fragment="",
            )
        ).rstrip("/")

    return urlunparse(parsed._replace(query="", fragment="")).rstrip("/")
