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
    """Strip tracking params; normalize host shortcuts; preserve content keys.

    Behaviour:
      - youtu.be/<id> → https://youtube.com/watch?v=<id>
      - x.com → twitter.com (aligns with NA convention)
      - drops query params named: utm_*, ref_*, mc_*, _hs* (prefix match),
        fbclid, gclid, yclid, msclkid (exact match)
      - keeps every other query param (including youtube v=, arxiv arch/id)
      - drops URL fragment
      - prepends https scheme if missing
      - normalizes empty path to "/"
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")

    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
        return f"https://youtube.com/watch?v={video_id}"

    if host == "x.com":
        host = "twitter.com"

    junk_prefixes = ("utm_", "ref_", "mc_", "_hs")
    junk_exact = {"fbclid", "gclid", "yclid", "msclkid"}
    qs = parse_qs(parsed.query, keep_blank_values=True)
    kept = {
        k: v
        for k, v in qs.items()
        if not any(k.startswith(p) for p in junk_prefixes) and k not in junk_exact
    }
    new_query = urlencode(kept, doseq=True)

    return urlunparse(
        (
            parsed.scheme or "https",
            host,
            parsed.path or "/",
            parsed.params,
            new_query,
            "",
        )
    )


def is_tier_a(content_type: str) -> bool:
    """Tier A = pipeline does heavy extraction (extract_complex_contents).
    Tier B = NA fetches on engagement (Article / Other)."""
    return content_type in _TIER_A
