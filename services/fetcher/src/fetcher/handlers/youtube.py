"""YouTube handler: transcript API + oEmbed metadata, with optional cloud structurer.

Audio transcription is the last tier: a video whose owner disabled captions
cannot be served by any caption source, so its audio is fetched and transcribed.
"""

import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fetcher.extractors import oembed as oembed_extractor
from fetcher.extractors import youtube_watch
from fetcher.metadata import build_metadata
from fetcher.extractors import transcript_structurer
from fetcher.extractors import youtube_transcript as transcript_extractor
from fetcher.extractors._cloud_chain import StructurerChainFailed
from fetcher.extractors import whisper as whisper_extractor
from fetcher.extractors.rapidapi import youtube_captions as rapidapi_captions_extractor
from fetcher.extractors.rapidapi import youtube_mp3 as youtube_mp3_extractor
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

    # oEmbed gives title/channel; the upload date comes from the watch page's SEO
    # microformat (oEmbed has none), fetched through the SOCKS5 proxy since a
    # data-center IP gets a consent-wall variant that strips it. Best-effort.
    upload_date = await youtube_watch.fetch_upload_date(
        ctx.socks5_url, url, timeout=ctx.upstream_timeout_s
    )
    # `chunks` is non-attribution sidecar junk the attribution metadata rides alongside.
    metadata: dict = {
        "chunks": chunks,
        **build_metadata(title=meta.title, authors=meta.author, published=upload_date),
    }
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


# Audio is streamed to disk before ffmpeg touches it; the cap stops a misreported
# link from filling the disk. Matches the file_audio handler's cap.
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

# A downloaded file shorter than the source by more than this is a partial
# download, not encoder jitter. Fixed allowance plus a proportion: a 4% gap is
# normal at this scale (measured), and a percentage alone is far too tight on
# short clips.
_DURATION_ALLOWANCE_S = 15.0
_DURATION_ALLOWANCE_RATIO = 0.10


async def _download_audio(ctx: FetchContext, url: str) -> Path:
    """Stream the converted MP3 to a tempfile. Caller owns deletion."""
    with tempfile.NamedTemporaryFile(prefix="youtube-audio-", suffix=".mp3", delete=False) as tmp:
        out_path = Path(tmp.name)

    total = 0
    async with ctx.http_client.stream(
        "GET", url, follow_redirects=True, timeout=ctx.upstream_timeout_s
    ) as response:
        response.raise_for_status()
        with open(out_path, "wb") as f:
            async for block in response.aiter_bytes(chunk_size=64 * 1024):
                f.write(block)
                total += len(block)
                if total > _MAX_DOWNLOAD_BYTES:
                    out_path.unlink(missing_ok=True)
                    raise ValueError(f"audio exceeds {_MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB cap")
    logger.info("youtube audio download: %d bytes from %s", total, url)
    return out_path


def _duration_shortfall_detail(measured: float, reported: float) -> str | None:
    """Detail string when the downloaded audio is materially shorter than the
    source the provider described, else None."""
    if reported <= 0:
        return None
    allowed = max(_DURATION_ALLOWANCE_S, reported * _DURATION_ALLOWANCE_RATIO)
    if reported - measured <= allowed:
        return None
    return (
        f"audio duration {measured:.1f}s is short of the reported source "
        f"duration {reported:.1f}s by more than {allowed:.1f}s — partial download"
    )


async def _rapidapi_mp3_tier(ctx: FetchContext, url: str) -> RawTierResult:
    """Transcribe the video's audio when no caption source could serve it.

    Produces the same chunk shape as the caption tiers and goes through the same
    finalizer, so the artifacts are interchangeable. The timestamps are ASR
    segment boundaries rather than published caption cues, so they are less
    precise — monotonic and citable, not frame-accurate.
    """
    if not ctx.rapidapi_key:
        return RawTierResult(
            content="", status=0, detail="rapidapi_mp3 skipped: RAPIDAPI_KEY not configured"
        )
    video_id = extract_video_id(url)
    if not video_id:
        return RawTierResult(content="", status=0)

    try:
        audio = await youtube_mp3_extractor.fetch_audio_link(
            ctx.http_client, video_id=video_id, api_key=ctx.rapidapi_key
        )
    except ValueError as exc:
        logger.warning("youtube rapidapi mp3 link fetch failed for %s: %s", video_id, exc)
        return RawTierResult(content="", status=0, detail=f"rapidapi_mp3: {_exception_detail(exc)}")

    try:
        audio_path = await _download_audio(ctx, audio.link)
    except Exception as exc:
        logger.warning("youtube audio download failed for %s: %s", video_id, exc)
        return RawTierResult(content="", status=0, detail=f"rapidapi_mp3: {_exception_detail(exc)}")

    chunk_dir: Path | None = None
    try:
        shortfall = _duration_shortfall_detail(
            whisper_extractor.probe_duration(audio_path), audio.duration
        )
        if shortfall is not None:
            logger.warning("youtube audio rejected for %s: %s", video_id, shortfall)
            return RawTierResult(content="", status=0, detail=f"rapidapi_mp3: {shortfall}")

        chunks = whisper_extractor.prepare_chunks(audio_path)
        if not chunks:
            return RawTierResult(
                content="", status=0, detail="rapidapi_mp3: ffmpeg produced no chunks"
            )
        chunk_dir = chunks[0].parent

        chain = whisper_extractor.get_chain()
        per_chunk = []
        for chunk in chunks:
            segments = await whisper_extractor.transcribe_chunk_verbose(ctx, chunk, chain=chain)
            per_chunk.append((segments, whisper_extractor.probe_duration(chunk)))
    except whisper_extractor.WhisperChainFailed as exc:
        logger.warning("youtube audio transcription failed for %s: %s", video_id, exc)
        return RawTierResult(content="", status=0, detail=f"rapidapi_mp3: {_exception_detail(exc)}")
    finally:
        if chunk_dir is not None:
            shutil.rmtree(chunk_dir, ignore_errors=True)
        audio_path.unlink(missing_ok=True)

    transcript_chunks = whisper_extractor.stitch_chunk_segments(per_chunk)
    if not transcript_chunks:
        return RawTierResult(content="", status=0, detail="rapidapi_mp3: transcription was empty")
    return await _finalize_chunks(ctx, url, transcript_chunks)


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
    # Own rate-limit key, not the shared "rapidapi" one: the cascade holds a
    # tier's semaphore for the tier's whole run, and this one spans polling, a
    # download, ffmpeg and several sequential transcriptions. Sharing would
    # block every other RapidAPI-backed tier for minutes.
    Tier(
        "rapidapi_mp3",
        "paid",
        200,
        200,
        _rapidapi_mp3_tier,
        rate_limit_key="youtube_audio",
    ),
]
