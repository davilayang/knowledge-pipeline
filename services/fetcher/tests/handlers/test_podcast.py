"""Tests for the podcast handler — MP3 / video-podcast URLs without YouTube mirror.

Path B of the podcast pipeline: when `podcast_canonicalize.maybe_redirect_*`
finds no YouTube mirror, the URL stays as an audio/video URL and this
handler runs Whisper directly.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fetcher.extractors import whisper
from fetcher.handlers import podcast


def test_registry_dispatches_mp3_url_to_podcast() -> None:
    """find_handler resolves an audio-native MP3 URL to the podcast handler
    (must come before the catch-all article handler in REGISTERED_HANDLERS)."""
    from fetcher.registry import find_handler

    handler = find_handler("https://dcs-cached.megaphone.fm/abc.mp3")
    assert handler is not None
    assert handler.NAME == "podcast"


def test_matches_mp3_url() -> None:
    """megaphone.fm-style audio-native MP3 URL → True."""
    url = "https://dcs-cached.megaphone.fm/SUPERDATASCIENCEPTYLTD7992118381.mp3"
    assert podcast.matches(url) is True


def test_does_not_match_html_url() -> None:
    """Plain article URL → False (would route to article handler)."""
    url = "https://www.example.com/post/some-essay.html"
    assert podcast.matches(url) is False


def test_matches_zencastr_mp4_url() -> None:
    """Zencastr-style video-podcast MP4 URL → True. Covers the 1.26 GB
    video-podcast case where ffmpeg has to strip video before Whisper."""
    url = (
        "https://media.zencastr.com/projects/abc/episodes/041/size/1258517258/"
        "stacked-data-041.mp4"
    )
    assert podcast.matches(url) is True


async def test_whisper_tier_happy_path_returns_structured_markdown(
    tiny_mp3: Path, tmp_path: Path
) -> None:
    """End-to-end orchestration: download → prepare_chunks → transcribe each →
    join → structurer → RawTierResult with header + structured body. Mocks
    only at the boundaries (download + LLM calls); ffmpeg chunking runs for real."""
    url = "https://example.com/episode-042.mp3"

    # Mock download: copy the tiny_mp3 fixture into a deterministic tempfile.
    audio_dest = tmp_path / "downloaded.mp3"
    audio_dest.write_bytes(tiny_mp3.read_bytes())

    async def fake_download(ctx, source_url):
        return audio_dest

    # Mock per-chunk transcription — return a fixed string per chunk.
    async def fake_transcribe(ctx, chunk_path, *, chain):
        return f"text-of-{chunk_path.name}"

    # Mock the cascade chain loader so structurer-enabled branch fires.
    fake_chain = [whisper.WhisperChainEntry(provider="groq", model="m", base_url="x")]

    # Mock structurer — return a fixed structured body.
    async def fake_structurer(ctx, raw, *, title, author, content_date=None):
        return ("STRUCTURED:" + raw[:50], "structurer:gpt-4.1-mini", {"tokens": 0})

    ctx = MagicMock()
    ctx.groq_api_key = "k"
    ctx.openai_api_key = "k"
    ctx.http_client = MagicMock()
    ctx.upstream_timeout_s = 300

    with (
        patch.object(podcast, "_download_audio", side_effect=fake_download),
        patch.object(whisper, "transcribe_chunk", side_effect=fake_transcribe),
        patch.object(whisper, "get_chain", return_value=fake_chain),
        patch(
            "fetcher.extractors.transcript_structurer.structure_transcript",
            side_effect=fake_structurer,
        ),
        patch(
            "fetcher.extractors.transcript_structurer.get_chain",
            return_value=[object()],
        ),
    ):
        result = await podcast.TIERS[0].run(ctx, url)

    assert result.status == 200
    assert "STRUCTURED:" in result.content
    assert result.metadata.get("chunk_count") == 1
    assert any(entry.tier == "transcript_structurer" for entry in result.extra_tier_log)
    # Audio + chunk tempfiles cleaned up
    assert not audio_dest.exists()


async def test_whisper_tier_falls_back_to_raw_when_structurer_fails(
    tiny_mp3: Path, tmp_path: Path
) -> None:
    """Structurer raises StructurerChainFailed → handler keeps raw transcript
    in markdown (status 200, structurer entry logs the failure). Mirrors
    the youtube handler's graceful-fallback contract."""
    from fetcher.extractors._cloud_chain import StructurerChainFailed

    url = "https://example.com/episode-043.mp3"
    audio_dest = tmp_path / "downloaded.mp3"
    audio_dest.write_bytes(tiny_mp3.read_bytes())

    async def fake_download(ctx, source_url):
        return audio_dest

    async def fake_transcribe(ctx, chunk_path, *, chain):
        return "raw chunk transcript text"

    async def failing_structurer(ctx, raw, *, title, author, content_date=None):
        raise StructurerChainFailed("all entries failed", retryable=True)

    ctx = MagicMock()
    ctx.groq_api_key = "k"
    ctx.openai_api_key = "k"
    ctx.upstream_timeout_s = 300

    with (
        patch.object(podcast, "_download_audio", side_effect=fake_download),
        patch.object(whisper, "transcribe_chunk", side_effect=fake_transcribe),
        patch.object(
            whisper,
            "get_chain",
            return_value=[whisper.WhisperChainEntry(provider="groq", model="m", base_url="x")],
        ),
        patch(
            "fetcher.extractors.transcript_structurer.structure_transcript",
            side_effect=failing_structurer,
        ),
        patch(
            "fetcher.extractors.transcript_structurer.get_chain",
            return_value=[object()],
        ),
    ):
        result = await podcast.TIERS[0].run(ctx, url)

    assert result.status == 200
    assert "raw chunk transcript text" in result.content
    structurer_entries = [e for e in result.extra_tier_log if e.tier == "transcript_structurer"]
    assert len(structurer_entries) == 1
    assert structurer_entries[0].error_kind == "exception"
