"""URL classification + canonicalization. Pure-Python; no I/O.

Adapted from newsletter-assistant's fetcher/orchestrator.py URL routing,
with the voice-tuned tier complexity stripped. `classify_content_type`
emits values that match kp's Notion `Content Type` SELECT options
(Article / YouTube / arXiv / Podcast / Other; PDF is in `ALL_CONTENT_TYPES`
for user override but never auto-emitted — PDF URLs fall through to
Article and the fetcher's pdf handler claims them). These get written
back to Notion as select-property values, so case + spelling must match
exactly.
"""

import re
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

_ARXIV_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
_ARXIV_PATH_PREFIXES = ("abs/", "pdf/", "html/")
# Mirrors NA's `arxiv_fetcher._NEW_ID_RE` / `_OLD_ID_RE` and kp fetcher's
# `services/fetcher/src/fetcher/handlers/arxiv.py` — three copies of the
# same regex, all required to match byte-for-byte. Update all three on any
# arxiv ID-format change; group(1) returns the version-stripped canonical
# ID. Duplicated rather than shared because the fetcher service is not a
# kp workspace member, and `classify.py` (in `orchestrators`) cannot depend
# on NA at all.
_NEW_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_OLD_ID_RE = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")


def _strip_pdf_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".pdf") else value


def _extract_arxiv_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _ARXIV_HOSTS:
        return None
    path = parsed.path.strip("/")
    if not path:
        return None
    if path.startswith(_ARXIV_PATH_PREFIXES):
        path = path.split("/", 1)[1]
    path = _strip_pdf_suffix(path)
    match = _NEW_ID_RE.match(path) or _OLD_ID_RE.match(path)
    return match.group(1) if match else None


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

    arxiv_id = _extract_arxiv_id(url)
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
