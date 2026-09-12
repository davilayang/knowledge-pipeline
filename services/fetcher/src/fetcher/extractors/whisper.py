"""Whisper transcription cascade — Groq primary → OpenAI fallback.

Direct httpx multipart POST to `/audio/transcriptions`. Both providers
expose the identical OpenAI-compatible endpoint shape, so we swap base_url
and the rest is identical.

Used by the upcoming `handlers/file_audio.py` for MP3 / video-podcast inputs
that have no YouTube mirror.
"""

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import yaml


if TYPE_CHECKING:
    from fetcher.types import FetchContext


logger = logging.getLogger(__name__)


_SEGMENT_SECONDS = 600


def prepare_chunks(input_path: Path, *, segment_seconds: int = _SEGMENT_SECONDS) -> list[Path]:
    """Normalize and chunk audio for Whisper in a single ffmpeg pass.

    Always strips video, downmixes to mono, resamples to 16 kHz, encodes at
    32 kbps MP3, and segments into ~`segment_seconds`-long chunks. Caller
    owns cleanup: `shutil.rmtree(chunks[0].parent)` removes everything.

    32 kbps mono @ 16 kHz: empirical floor for Whisper WER — no measurable
    accuracy loss vs 64 kbps; half the size.
    """
    chunk_dir = Path(tempfile.mkdtemp(prefix="whisper-chunks-"))
    pattern = str(chunk_dir / "chunk_%03d.mp3")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "32k",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            pattern,
        ],
        check=True,
        capture_output=True,
    )
    return sorted(chunk_dir.glob("chunk_*.mp3"))


@dataclass(frozen=True)
class WhisperChainEntry:
    provider: str
    model: str
    base_url: str
    attempt_timeout: float = 300.0


class WhisperChainFailed(Exception):
    """Raised when every entry in the whisper chain failed."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class WhisperNotConfigured(WhisperChainFailed):
    """Permanent: chain has entries but no provider has a configured API key.
    Distinct subclass so handler maps to a 'skip whisper, try elsewhere' branch
    via isinstance — mirrors StructurerNotConfigured in `_cloud_chain.py`."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def probe_duration(path: Path) -> float:
    """Measured duration in seconds — the authority for the stitched timeline.

    Not the nominal split length (ffmpeg cuts on keyframes, last chunk is short)
    and not the provider's self-reported duration (rounded, sometimes absent,
    and its error accumulates across every later chunk).
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def segments_to_chunks(
    segments: list[dict], *, offset: float, chunk_duration: float | None = None
) -> list[dict]:
    """Map Whisper `{start, end, text}` segments onto the caption-chunk shape.

    `offset` shifts timestamps onto the full recording's timeline — Whisper
    restarts at 0 for every chunk it is given. Segments past `chunk_duration`
    are hallucinated tails; those and malformed ones are dropped rather than
    repaired, since nothing downstream could tell a guess from real data.
    """
    chunks: list[dict] = []
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start or start < 0:
            continue
        if chunk_duration is not None and end > chunk_duration:
            continue
        text = segment.get("text", "")
        if not text.strip():
            continue
        chunks.append(
            {
                "text": text,
                "start": round(start + offset, 3),
                "duration": round(end - start, 3),
            }
        )
    return chunks


def stitch_chunk_segments(per_chunk: list[tuple[list[dict], float]]) -> list[dict]:
    """Flatten per-chunk `(segments, measured_duration)` pairs onto one timeline.

    Each chunk's offset is the sum of the measured durations before it — see
    `probe_duration` for why measured rather than nominal.
    """
    chunks: list[dict] = []
    offset = 0.0
    for segments, measured_duration in per_chunk:
        chunks.extend(segments_to_chunks(segments, offset=offset, chunk_duration=measured_duration))
        offset += measured_duration
    return chunks


def _key_for(provider: str, ctx: "FetchContext") -> str | None:
    if provider == "groq":
        return ctx.groq_api_key
    if provider == "openai":
        return ctx.openai_api_key
    return None


_KNOWN_PROVIDERS = {"groq", "openai"}


def _load_chain(path: Path) -> list[WhisperChainEntry]:
    """Parse the whisper chain YAML. Returns [] if the file is missing."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("whisper chain YAML not found at %s; whisper unreachable", path)
        return []
    entries: list[WhisperChainEntry] = []
    for raw in data.get("chain") or []:
        provider = str(raw["provider"])
        if provider not in _KNOWN_PROVIDERS:
            raise ValueError(f"unknown provider {provider!r} in {path}")
        entries.append(
            WhisperChainEntry(
                provider=provider,
                model=str(raw["model"]),
                base_url=str(raw["base_url"]),
                attempt_timeout=float(raw.get("attempt_timeout", 300.0)),
            )
        )
    return entries


_CHAIN: list[WhisperChainEntry] = _load_chain(
    Path(os.environ.get("FETCHER_WHISPER_CONFIG_PATH", "config/whisper.yaml"))
)


def get_chain() -> list[WhisperChainEntry]:
    return list(_CHAIN)


async def _post_to_chain(
    ctx: "FetchContext",
    chunk_path: Path,
    *,
    chain: list[WhisperChainEntry],
    data: dict,
) -> httpx.Response:
    """POST the chunk to each chain entry in order; return the first 2xx response.

    `data` carries the form fields other than `model` — the only thing differing
    between the plain-text and timestamped callers.
    """
    callable_entries = [e for e in chain if _key_for(e.provider, ctx) is not None]
    if not callable_entries:
        raise WhisperNotConfigured(
            f"no API keys configured for any whisper chain provider "
            f"(chain has {len(chain)} entries, none have a matching key)"
        )

    last_exc: BaseException | None = None
    for entry in callable_entries:
        api_key = _key_for(entry.provider, ctx)
        try:
            with chunk_path.open("rb") as f:
                resp = await ctx.http_client.post(
                    f"{entry.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (chunk_path.name, f, "audio/mpeg")},
                    data={"model": entry.model, **data},
                    timeout=entry.attempt_timeout,
                )
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return resp
        except Exception as exc:  # noqa: BLE001 — bounded fall-through across chain
            last_exc = exc
            logger.warning(
                "whisper tier failed: provider=%s model=%s %s: %.200s",
                entry.provider,
                entry.model,
                type(exc).__name__,
                exc,
            )
            continue

    detail = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "all entries failed"
    raise WhisperChainFailed(detail)


async def transcribe_chunk(
    ctx: "FetchContext",
    chunk_path: Path,
    *,
    chain: list[WhisperChainEntry],
) -> str:
    """Try each chain entry in order. Returns transcript text from the first
    tier that succeeds."""
    resp = await _post_to_chain(ctx, chunk_path, chain=chain, data={"response_format": "text"})
    return resp.text.strip()


async def transcribe_chunk_verbose(
    ctx: "FetchContext",
    chunk_path: Path,
    *,
    chain: list[WhisperChainEntry],
) -> list[dict]:
    """Like `transcribe_chunk`, but returns Whisper's timestamped segments.

    Separate from `transcribe_chunk` so that keeps its `str` return for existing
    callers. Segments come from `verbose_json` alone — Groq rejects OpenAI's
    `timestamp_granularities` param outright (HTTP 400 `unknown_param`).
    """
    resp = await _post_to_chain(
        ctx, chunk_path, chain=chain, data={"response_format": "verbose_json"}
    )
    return resp.json().get("segments") or []
