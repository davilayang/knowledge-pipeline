"""URL classification + canonicalization. Pure-Python; no I/O.

Adapted from newsletter-assistant's fetcher/orchestrator.py URL routing,
with the voice-tuned tier complexity stripped. Output values match kp's
Notion `Content Type` SELECT options (Article / YouTube / arXiv / Other)
rather than NA's fetcher-tier names — these get written back to Notion
as select-property values, so case + spelling must match exactly.
"""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

CONTENT_TYPE_ARTICLE = "Article"
CONTENT_TYPE_YOUTUBE = "YouTube"
CONTENT_TYPE_ARXIV = "arXiv"
CONTENT_TYPE_PDF = "PDF"
CONTENT_TYPE_PODCAST = "Podcast"
CONTENT_TYPE_OTHER = "Other"

ALL_CONTENT_TYPES = {
    CONTENT_TYPE_ARTICLE,
    CONTENT_TYPE_YOUTUBE,
    CONTENT_TYPE_ARXIV,
    CONTENT_TYPE_PDF,
    CONTENT_TYPE_PODCAST,
    CONTENT_TYPE_OTHER,
}

_TIER_A = {CONTENT_TYPE_YOUTUBE, CONTENT_TYPE_ARXIV, CONTENT_TYPE_PDF, CONTENT_TYPE_PODCAST}


def classify_content_type(url: str) -> str:
    """Pure URL → kp Content Type.

    Returns one of:
      - "YouTube" for youtube.com / youtu.be / m.youtube.com / music.youtube.com
      - "arXiv" for arxiv.org and any subdomain ending in .arxiv.org
      - "Article" as the default fallback for any other host

    PDF and Podcast classifications are intentionally NOT emitted in v1
    — the Notion Content Type SELECT only has Article / YouTube / arXiv
    / Other options. PDF and Podcast URLs fall through to Article (Tier B
    treatment; NA fetches at engagement). The PDF/Podcast match arms are
    left in the source as commented-out stubs, ready to uncomment when
    the Notion options + fetchers land.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")

    match host:
        case "youtube.com" | "m.youtube.com" | "music.youtube.com" | "youtu.be":
            return CONTENT_TYPE_YOUTUBE
        case h if h == "arxiv.org" or h.endswith(".arxiv.org"):
            return CONTENT_TYPE_ARXIV
        # PDF + Podcast options don't exist on Notion yet — fall through to Article.
        # case _ if (parsed.path or "").lower().endswith(".pdf"):
        #     return CONTENT_TYPE_PDF
        # case "podcasts.apple.com" if "/podcast/" in (parsed.path or "").lower():
        #     return CONTENT_TYPE_PODCAST
        # case "open.spotify.com" if (parsed.path or "").lower().startswith("/episode/"):
        #     return CONTENT_TYPE_PODCAST
        case _:
            return CONTENT_TYPE_ARTICLE


def canonicalize_url(url: str) -> str:
    """Output must equal newsletter-assistant's `normalize_url`
    (`packages/knowledge/src/knowledge/fetcher/orchestrator.py`) — NA's
    `kp_queue_cache` looks up by this exact string; drift = silent miss."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").removeprefix("www.")

    if hostname in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        v = parse_qs(parsed.query).get("v", [""])[0]
        query = urlencode({"v": v}) if v else ""
        return urlunparse(parsed._replace(query=query, fragment="")).rstrip("/")

    return urlunparse(parsed._replace(query="", fragment="")).rstrip("/")


def is_tier_a(content_type: str) -> bool:
    """Tier A = pipeline does heavy extraction (extract_complex_contents).
    Tier B = NA fetches on engagement (Article / Other)."""
    return content_type in _TIER_A
