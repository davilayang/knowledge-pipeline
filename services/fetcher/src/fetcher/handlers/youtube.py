"""YouTube handler: transcript API + oEmbed metadata, with optional cloud structurer."""

import logging
import re
import time
from urllib.parse import parse_qs, urlparse

from fetcher.extractors import oembed as oembed_extractor
from fetcher.metadata import build_metadata
from fetcher.extractors import transcript_structurer
from fetcher.extractors import youtube_transcript as transcript_extractor
from fetcher.extractors._cloud_chain import StructurerChainFailed
from fetcher.extractors.rapidapi import youtube_captions as rapidapi_captions_extractor
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
    from youtube_transcript_api.proxies import GenericProxyConfig

    video_id = extract_video_id(url)
    if not video_id:
        return RawTierResult(content="", status=0)

    # YouTube IP-blocks data-center ranges (Hetzner, AWS, etc.) on the
    # transcript endpoint. Route through ctx.socks5_url when set — same
    # Tailscale → residential-IP path the article handler uses.
    proxy_config = (
        GenericProxyConfig(http_url=ctx.socks5_url, https_url=ctx.socks5_url)
        if ctx.socks5_url
        else None
    )

    try:
        api = YouTubeTranscriptApi(proxy_config=proxy_config)
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
        return RawTierResult(content="", status=0, detail=_exception_detail(exc))
    except Exception as exc:
        logger.warning("youtube transcript fetch failed for %s: %s", video_id, exc)
        return RawTierResult(content="", status=0, detail=_exception_detail(exc))

    return await _finalize_chunks(ctx, url, chunks)


async def _finalize_chunks(ctx: FetchContext, url: str, chunks: list[dict]) -> RawTierResult:
    """Common finalization for any tier that produces a transcript chunk list.

    Oembed metadata + markdown header + body + optional structurer pass —
    shared by `_transcript_api_tier` (youtube-transcript-api via SOCKS5)
    and `_rapidapi_captions_tier` (youtube-data16 via RapidAPI). Both
    tiers produce identical artifacts; only the chunk-acquisition source
    differs."""
    meta = await oembed_extractor.youtube_metadata(ctx.http_client, url)
    header = _format_header(meta, url)
    body = transcript_extractor.chunks_to_markdown(chunks)
    raw_markdown = header + body

    # oEmbed title/channel are provenance — surface them as canonical metadata,
    # not just in the header. `chunks` is non-attribution sidecar junk it rides
    # alongside. (Upload date is absent from oEmbed — a separate source, deferred.)
    metadata: dict = {"chunks": chunks, **build_metadata(title=meta.title, authors=meta.author)}
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


async def _rapidapi_captions_tier(ctx: FetchContext, url: str) -> RawTierResult:
    """Paid fallback for when the free transcript_api tier returns no
    chunks (IP-blocked even via Tailscale, no community transcript indexed
    under en, etc.). Hits youtube-data16 via RapidAPI for the same
    text/start/duration chunks and feeds the shared finalization helper."""
    if not ctx.rapidapi_key:
        return RawTierResult(
            content="", status=0, detail="rapidapi_captions skipped: RAPIDAPI_KEY not configured"
        )
    video_id = extract_video_id(url)
    if not video_id:
        return RawTierResult(content="", status=0)
    try:
        chunks = await rapidapi_captions_extractor.fetch_captions(
            ctx.http_client, video_id=video_id, api_key=ctx.rapidapi_key
        )
    except ValueError as exc:
        logger.warning("youtube rapidapi captions fetch failed for %s: %s", video_id, exc)
        return RawTierResult(
            content="", status=0, detail=f"rapidapi_captions: {_exception_detail(exc)}"
        )
    return await _finalize_chunks(ctx, url, chunks)


def _exception_detail(exc: BaseException) -> str:
    """Single-line detail string for the tier_log — class name + truncated message."""
    msg = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {msg}"[:500] if msg else type(exc).__name__


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
    Tier(
        "rapidapi_captions",
        "paid",
        200,
        200,
        _rapidapi_captions_tier,
        rate_limit_key="rapidapi",
    ),
]
