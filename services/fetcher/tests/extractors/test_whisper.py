"""Tests for the whisper cascade (Groq primary → OpenAI fallback).

Direct httpx multipart POST to `/audio/transcriptions` on both providers —
no `openai` / `groq` SDK imports. Both expose the identical OpenAI-compat
endpoint shape.
"""

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx


def _make_response(status: int, text: str = "") -> httpx.Response:
    """Construct a real httpx.Response so raise_for_status / .text behave."""
    return httpx.Response(status_code=status, text=text)


async def test_transcribe_chunk_falls_back_to_openai_when_groq_fails(
    tmp_path: Path,
) -> None:
    """Groq POST raises a connect error → OpenAI POST returns 200 'hello'.
    Asserts the returned transcript comes from the second tier."""
    from fetcher.extractors import whisper

    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"fake-audio-bytes")

    chain = [
        whisper.WhisperChainEntry(
            provider="groq",
            model="whisper-large-v3-turbo",
            base_url="https://api.groq.com/openai/v1",
        ),
        whisper.WhisperChainEntry(
            provider="openai",
            model="whisper-1",
            base_url="https://api.openai.com/v1",
        ),
    ]

    async def fake_post(url, **kwargs):
        if "groq" in url:
            raise httpx.ConnectError("connect timeout")
        return _make_response(200, "hello")

    ctx = MagicMock()
    ctx.groq_api_key = "groq-sk"
    ctx.openai_api_key = "openai-sk"
    ctx.http_client = MagicMock()
    ctx.http_client.post = AsyncMock(side_effect=fake_post)

    text = await whisper.transcribe_chunk(ctx, chunk, chain=chain)

    assert text == "hello"
    assert ctx.http_client.post.await_count == 2


async def test_transcribe_chunk_raises_not_configured_when_no_key(tmp_path: Path) -> None:
    """Chain has entries but neither provider's API key is set on ctx.
    Raises WhisperNotConfigured (subclass) so the handler can skip-and-detail
    via isinstance — mirrors StructurerNotConfigured."""
    from fetcher.extractors import whisper

    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"fake-audio-bytes")

    chain = [
        whisper.WhisperChainEntry(
            provider="groq",
            model="whisper-large-v3-turbo",
            base_url="https://api.groq.com/openai/v1",
        ),
        whisper.WhisperChainEntry(
            provider="openai",
            model="whisper-1",
            base_url="https://api.openai.com/v1",
        ),
    ]

    ctx = MagicMock()
    ctx.groq_api_key = None
    ctx.openai_api_key = None
    ctx.http_client = MagicMock()
    ctx.http_client.post = AsyncMock(side_effect=AssertionError("should not POST"))

    try:
        await whisper.transcribe_chunk(ctx, chunk, chain=chain)
        raise AssertionError("expected WhisperNotConfigured")
    except whisper.WhisperNotConfigured as exc:
        assert "no API keys configured" in str(exc)
        assert exc.retryable is False


def test_not_configured_is_a_chain_failed_subclass() -> None:
    from fetcher.extractors import whisper

    assert issubclass(whisper.WhisperNotConfigured, whisper.WhisperChainFailed)


async def test_transcribe_chunk_returns_groq_text_on_happy_path(tmp_path: Path) -> None:
    """First chain entry succeeds → its text returned, OpenAI tier never called.
    Pins the off-by-one (don't walk past first tier on success)."""
    from fetcher.extractors import whisper

    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"fake-audio-bytes")

    chain = [
        whisper.WhisperChainEntry(
            provider="groq",
            model="whisper-large-v3-turbo",
            base_url="https://api.groq.com/openai/v1",
        ),
        whisper.WhisperChainEntry(
            provider="openai",
            model="whisper-1",
            base_url="https://api.openai.com/v1",
        ),
    ]

    async def fake_post(url, **kwargs):
        if "groq" in url:
            return _make_response(200, "Hello world")
        raise AssertionError("openai tier should not be called")

    ctx = MagicMock()
    ctx.groq_api_key = "groq-sk"
    ctx.openai_api_key = "openai-sk"
    ctx.http_client = MagicMock()
    ctx.http_client.post = AsyncMock(side_effect=fake_post)

    text = await whisper.transcribe_chunk(ctx, chunk, chain=chain)

    assert text == "Hello world"
    assert ctx.http_client.post.await_count == 1


# ---------------------------------------------------------------------------
# Phase B — ffmpeg normalize-and-chunk (single pass)
# ---------------------------------------------------------------------------


def _ffprobe_codec_types(path: Path) -> set[str]:
    import json
    import subprocess

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return {s["codec_type"] for s in json.loads(probe.stdout)["streams"]}


def test_prepare_chunks_strips_video_from_mp4(tiny_mp4: Path) -> None:
    """1-second MP4 input → list of 1 audio-only MP3 chunk.
    Verifies the actual ffmpeg pipeline end-to-end: ffprobe confirms no
    video stream survives, audio stream present, output is decodable."""
    from fetcher.extractors import whisper

    chunks = whisper.prepare_chunks(tiny_mp4)
    try:
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.exists()
        assert chunk.suffix == ".mp3"
        assert _ffprobe_codec_types(chunk) == {"audio"}
    finally:
        shutil.rmtree(chunks[0].parent, ignore_errors=True)


def test_prepare_chunks_returns_chunks_in_isolated_dir(tiny_mp4: Path) -> None:
    """All chunks live in a single fresh directory; rmtree on that dir
    removes them all (and nothing else). Caller can run cleanup via one
    `shutil.rmtree(chunks[0].parent)` regardless of chunk count."""
    from fetcher.extractors import whisper

    chunks = whisper.prepare_chunks(tiny_mp4)
    chunk_dir = chunks[0].parent

    siblings = sorted(chunk_dir.iterdir())
    assert siblings == sorted(chunks), "chunk dir should contain only chunks"

    shutil.rmtree(chunk_dir)
    assert not chunk_dir.exists()


def test_prepare_chunks_splits_long_input_into_multiple_chunks(medium_mp3: Path) -> None:
    """25-second input with segment_time=10 → at least 2 chunks, returned
    in declared (lexicographic) order so downstream concatenation preserves
    episode order."""
    from fetcher.extractors import whisper

    chunks = whisper.prepare_chunks(medium_mp3, segment_seconds=10)
    try:
        assert len(chunks) >= 2
        assert chunks == sorted(chunks)
        for chunk in chunks:
            assert chunk.exists()
            assert _ffprobe_codec_types(chunk) == {"audio"}
    finally:
        shutil.rmtree(chunks[0].parent, ignore_errors=True)
