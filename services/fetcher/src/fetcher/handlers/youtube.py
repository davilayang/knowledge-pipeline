"""YouTube handler: transcript API plus oEmbed metadata."""

import logging
import re
from urllib.parse import parse_qs, urlparse

from fetcher.extractors import oembed as oembed_extractor
from fetcher.extractors import youtube_transcript as transcript_extractor
from fetcher.types import FetchContext, RawTierResult, Tier


logger = logging.getLogger(__name__)

NAME = "youtube"
STRICT_PAID_TIER = False

_YOUTUBE_DOMAINS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_YOUTUBE_VIDEO_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_DOMAINS:
        if parsed.path.startswith(("/embed/", "/v/", "/shorts/")):
            parts = parsed.path.split("/")
            video_id = parts[2] if len(parts) > 2 else ""
        else:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        return None
    return video_id if _YOUTUBE_VIDEO_RE.match(video_id) else None


def matches(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return False
    return host in _YOUTUBE_DOMAINS and extract_video_id(url) is not None


async def _transcript_api_tier(ctx: FetchContext, url: str) -> RawTierResult:
    from youtube_transcript_api import (
        AgeRestricted,
        IpBlocked,
        NoTranscriptFound,
        RequestBlocked,
        TranscriptsDisabled,
        VideoUnavailable,
        VideoUnplayable,
        YouTubeTranscriptApi,
    )

    video_id = extract_video_id(url)
    if not video_id:
        return RawTierResult(content="", status=0)

    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        chunks = [
            {"text": snippet.text, "start": snippet.start, "duration": snippet.duration}
            for snippet in transcript.snippets
        ]
    except (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        VideoUnplayable,
        AgeRestricted,
        IpBlocked,
        RequestBlocked,
    ) as exc:
        logger.info("youtube transcript unavailable for %s: %s", video_id, type(exc).__name__)
        return RawTierResult(content="", status=0)
    except Exception as exc:
        logger.warning("youtube transcript fetch failed for %s: %s", video_id, exc)
        return RawTierResult(content="", status=0)

    header = await oembed_extractor.youtube_metadata_header(ctx.http_client, url)
    body = transcript_extractor.chunks_to_markdown(chunks)
    return RawTierResult(content=header + body, status=200)


TIERS: list[Tier] = [
    Tier("transcript_api", "free", 200, 200, _transcript_api_tier),
]
