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


_AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".wav", ".opus")


def classify_content_type(url: str) -> str:
    """Pure URL → kp Content Type.

    Returns one of:
      - "YouTube" for youtube.com / youtu.be / m.youtube.com / music.youtube.com
      - "arXiv" for arxiv.org and any subdomain ending in .arxiv.org
      - "Podcast" for audio file suffixes (.mp3 / .m4a / .ogg / .wav / .opus)
        — covers podtrac redirects, libsyn, megaphone, etc.
      - "Article" as the default fallback for any other host

    PDF classification is intentionally NOT emitted — the Notion Content
    Type SELECT does not have a PDF option. PDF URLs fall through to
    Article (the fetcher's pdf handler still claims them via the registry).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.")
    path = (parsed.path or "").lower()

    match host:
        case "youtube.com" | "m.youtube.com" | "music.youtube.com" | "youtu.be":
            return CONTENT_TYPE_YOUTUBE
        case h if h == "arxiv.org" or h.endswith(".arxiv.org"):
            return CONTENT_TYPE_ARXIV
        case _ if path.endswith(_AUDIO_SUFFIXES):
            return CONTENT_TYPE_PODCAST
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
