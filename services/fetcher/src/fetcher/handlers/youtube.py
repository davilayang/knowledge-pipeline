"""YouTube handler: transcript API + oEmbed metadata, with optional cloud structurer."""

import logging
import re
import time
from urllib.parse import parse_qs, urlparse

from fetcher.extractors import oembed as oembed_extractor
from fetcher.extractors import transcript_structurer
from fetcher.extractors import youtube_transcript as transcript_extractor
from fetcher.extractors._cloud_chain import StructurerChainFailed
from fetcher.types import FetchContext, RawTierResult, Tier, TierLogEntry


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

    meta = await oembed_extractor.youtube_metadata(ctx.http_client, url)
    header = _format_header(meta, url)
    body = transcript_extractor.chunks_to_markdown(chunks)
    raw_markdown = header + body

    metadata: dict = {"chunks": chunks}
    extra_log: list[TierLogEntry] = []
    final_markdown = raw_markdown

    if ctx.youtube_structurer_enabled and transcript_structurer.get_chain():
        structured, struct_entry, struct_meta = await _run_structurer(
            ctx, body, title=meta.title, author=meta.author
        )
        if structured is not None:
            final_markdown = header + structured
            metadata.update(struct_meta)
        extra_log.append(struct_entry)

    return RawTierResult(
        content=final_markdown,
        status=200,
        metadata=metadata,
        extra_tier_log=extra_log,
    )


def _format_header(meta: oembed_extractor.YouTubeMetadata, url: str) -> str:
    title = meta.title or "Untitled"
    if meta.author:
        return f"# {title}\n\n**Channel:** {meta.author}\n**Source:** {url}\n\n---\n\n"
    return f"# {title}\n\n**Source:** {url}\n\n---\n\n"


async def _run_structurer(
    ctx: FetchContext,
    raw_body: str,
    *,
    title: str | None,
    author: str | None,
) -> tuple[str | None, TierLogEntry, dict]:
    """Run the cloud transcript structurer.

    Returns (structured_text_or_None, tier_log_entry, metadata_dict).
    On failure: structured is None, caller keeps raw markdown.
    """
    t0 = time.monotonic()
    try:
        structured, tier_name, usage = await transcript_structurer.structure_transcript(
            ctx, raw_body, title=title, author=author
        )
    except StructurerChainFailed as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("youtube transcript structurer failed: %s", exc)
        entry = TierLogEntry(
            tier="transcript_structurer",
            status=0,
            chars=0,
            error="empty",
            validated=False,
            duration_ms=duration_ms,
            error_kind="exception",
            detail=f"StructurerChainFailed: {exc}"[:500],
        )
        return None, entry, {}

    duration_ms = int((time.monotonic() - t0) * 1000)
    entry = TierLogEntry(
        tier="transcript_structurer",
        status=200,
        chars=len(structured),
        error=None,
        validated=True,
        duration_ms=duration_ms,
        error_kind="ok",
    )
    return structured, entry, {"structurer_tier": tier_name, "structurer_usage": usage}


TIERS: list[Tier] = [
    Tier("transcript_api", "free", 200, 200, _transcript_api_tier),
]
