"""YouTube fetcher — transcript via youtube-transcript-api + oEmbed metadata."""

import logging
import re
from urllib.parse import parse_qs, quote, urlparse

import httpx
from youtube_transcript_api import (
    AgeRestricted,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
)

from .result import FetchResult

logger = logging.getLogger(__name__)

# After removeprefix("www."), hostnames are checked against this set.
_YOUTUBE_DOMAINS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_YOUTUBE_VIDEO_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video link."""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return False
    hostname = hostname.removeprefix("www.")
    return hostname in _YOUTUBE_DOMAINS and extract_video_id(url) is not None


def extract_video_id(url: str) -> str | None:
    """Extract the 11-char video ID from a YouTube URL."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").removeprefix("www.")

    if hostname == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif hostname in _YOUTUBE_DOMAINS:
        if parsed.path.startswith(("/embed/", "/v/", "/shorts/")):
            video_id = parsed.path.split("/")[2]
        else:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        return None

    return video_id if _YOUTUBE_VIDEO_RE.match(video_id) else None


def _build_api(*, proxy_url: str | None):  # -> YouTubeTranscriptApi
    """Build API client, with optional proxy for deployments where YouTube blocks the IP."""
    from youtube_transcript_api import YouTubeTranscriptApi

    if proxy_url:
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy = GenericProxyConfig(https_url=proxy_url)
        logger.info("Using YouTube proxy: %s", proxy_url)
        return YouTubeTranscriptApi(proxy_config=proxy)

    return YouTubeTranscriptApi()


def _fetch_oembed(url: str) -> dict:
    """Fetch oEmbed metadata for a YouTube video (no API key needed).

    Returns the parsed JSON dict, or empty dict on failure.
    """
    try:
        resp = httpx.get(
            f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json",
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("_fetch_oembed: returned %d for %s", resp.status_code, url)
            return {}
        return resp.json()
    except Exception as exc:
        logger.warning("_fetch_oembed: failed for %s: %s", url, exc)
        return {}


def fetch(url: str, *, proxy_url: str | None = None) -> FetchResult:
    """Fetch transcript + metadata from a YouTube video.

    Returns a FetchResult with transcript text, title, and author (channel name).
    The transcript is fetched via youtube-transcript-api (routed through proxy_url
    if provided). Metadata comes from YouTube oEmbed.
    """
    video_id = extract_video_id(url)
    if not video_id:
        logger.warning("fetch: could not extract video ID from %s", url)
        return FetchResult(error="invalid YouTube URL")

    try:
        api = _build_api(proxy_url=proxy_url)
        transcript = api.fetch(video_id)
        text = " ".join(snippet.text for snippet in transcript.snippets)
        logger.info(
            "fetch: got %d chars (lang=%s, generated=%s)",
            len(text),
            transcript.language_code,
            transcript.is_generated,
        )
    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        logger.info("fetch: no transcript for %s (%s)", url, reason)
        return FetchResult(error=reason)
    except (IpBlocked, RequestBlocked) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("fetch: blocked by youtube for %s — %s", url, reason)
        return FetchResult(error=reason)
    except (AgeRestricted, VideoUnplayable, VideoUnavailable) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("fetch: video unavailable for %s — %s", url, reason)
        return FetchResult(error=reason)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        logger.error("fetch: unexpected error for %s — %s", url, reason, exc_info=True)
        return FetchResult(error=reason)

    oembed = _fetch_oembed(url)

    return FetchResult(
        content=text,
        tier="youtube",
        tier_log=[{"tier": "youtube", "status": "ok", "chars": len(text)}],
        title=oembed.get("title", ""),
        author=oembed.get("author_name", ""),
    )
