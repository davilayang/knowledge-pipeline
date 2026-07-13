"""file_audio handler: Whisper transcription for audio/video-file URLs
(mp3 / m4a / mp4 / … — a raw media file at a URL, no YouTube mirror)."""

import logging
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from domains.content_urls import AUDIO_SUFFIXES
from fetcher.extractors import transcript_structurer
from fetcher.extractors import whisper as whisper_extractor
from fetcher.extractors._cloud_chain import StructurerChainFailed
from fetcher.types import FetchContext, RawTierResult, Tier, TierLogEntry


logger = logging.getLogger(__name__)


NAME = "file_audio"
STRICT_PAID_TIER = False

# Audio + video suffixes shared with domains.classify_url_type so routing and
# classification agree on the set (whisper strips video to audio before ASR).
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB — covers Zencastr-style video podcasts


def matches(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return parsed.path.lower().endswith(AUDIO_SUFFIXES)


async def _download_audio(ctx: FetchContext, url: str) -> Path:
    """Stream-download the audio to a tempfile. Aborts beyond
    `_MAX_DOWNLOAD_BYTES` so a misbehaving Zencastr URL can't fill disk."""
    parsed_suffix = Path(urlparse(url).path).suffix.lower()
    suffix = parsed_suffix if parsed_suffix in AUDIO_SUFFIXES else ".bin"
    with tempfile.NamedTemporaryFile(prefix="file-audio-dl-", suffix=suffix, delete=False) as tmp:
        out_path = Path(tmp.name)

    total = 0
    async with ctx.http_client.stream(
        "GET", url, follow_redirects=True, timeout=ctx.upstream_timeout_s
    ) as response:
        response.raise_for_status()
        with open(out_path, "wb") as f:
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                f.write(chunk)
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    out_path.unlink(missing_ok=True)
                    raise ValueError(f"audio exceeds {_MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB cap")
    logger.info("file_audio download: %d bytes from %s", total, url)
    return out_path


async def _whisper_tier(ctx: FetchContext, url: str) -> RawTierResult:
    try:
        audio_path = await _download_audio(ctx, url)
    except Exception as exc:
        logger.warning("file_audio download failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0, detail=_exception_detail(exc))

    chunk_dir: Path | None = None
    try:
        chunks = whisper_extractor.prepare_chunks(audio_path)
        if not chunks:
            return RawTierResult(content="", status=0, detail="ffmpeg produced no chunks")
        chunk_dir = chunks[0].parent

        chain = whisper_extractor.get_chain()
        transcript_parts: list[str] = []
        for chunk in chunks:
            text = await whisper_extractor.transcribe_chunk(ctx, chunk, chain=chain)
            transcript_parts.append(text)
        transcript = "\n\n".join(transcript_parts)
    except whisper_extractor.WhisperChainFailed as exc:
        logger.warning("file_audio whisper transcription failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0, detail=_exception_detail(exc))
    finally:
        if chunk_dir is not None:
            shutil.rmtree(chunk_dir, ignore_errors=True)
        audio_path.unlink(missing_ok=True)

    return await _finalize_transcript(ctx, url, transcript, chunk_count=len(chunks))


async def _finalize_transcript(
    ctx: FetchContext, url: str, transcript: str, *, chunk_count: int
) -> RawTierResult:
    header = _format_header(url)
    metadata: dict = {"chunk_count": chunk_count, "transcript_chars": len(transcript)}
    extra_log: list[TierLogEntry] = []
    final_markdown = header + transcript

    if transcript_structurer.get_chain():
        structured, struct_entry, struct_meta = await _run_structurer(ctx, transcript)
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


async def _run_structurer(
    ctx: FetchContext, transcript: str
) -> tuple[str | None, TierLogEntry, dict]:
    t0 = time.monotonic()
    try:
        structured, tier_name, usage = await transcript_structurer.structure_transcript(
            ctx, transcript, title=None, author=None
        )
    except StructurerChainFailed as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("file_audio transcript structurer failed: %s", exc)
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


def _format_header(url: str) -> str:
    return f"**Source:** {url}\n\n---\n\n"


def _exception_detail(exc: BaseException) -> str:
    msg = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {msg}"[:500] if msg else type(exc).__name__


TIERS: list[Tier] = [
    Tier("whisper", "paid", 200, 200, _whisper_tier, rate_limit_key="whisper"),
]
