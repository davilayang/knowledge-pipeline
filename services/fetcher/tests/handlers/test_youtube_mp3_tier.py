"""The YouTube handler's audio-transcription tier — last resort for videos whose
owner disabled captions. Goes through the same finalizer as the caption tiers,
so a transcribed video yields the same artifacts as a captioned one.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher.extractors.rapidapi.youtube_mp3 import AudioLink
from fetcher.handlers import youtube


_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def _no_watch_date():
    with patch(
        "fetcher.handlers.youtube.youtube_watch.fetch_upload_date",
        new=AsyncMock(return_value=None),
    ):
        yield


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.rapidapi_key = "rapid-key"
    ctx.groq_api_key = "groq-key"
    ctx.openai_api_key = "openai-key"
    ctx.youtube_structurer_enabled = False
    ctx.upstream_timeout_s = 30
    ctx.http_client = MagicMock()
    return ctx


def _patch_oembed():
    from fetcher.extractors.oembed import YouTubeMetadata

    return patch(
        "fetcher.handlers.youtube.oembed_extractor.youtube_metadata",
        AsyncMock(return_value=YouTubeMetadata(title="T", author="C", source_url=_VIDEO_URL)),
    )


def _patch_pipeline(*, source_duration: float, measured_duration: float, tmp_path: Path):
    """Patch the audio pipeline around the tier: link fetch, download, ffmpeg, ASR."""
    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"x")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")

    return (
        patch(
            "fetcher.handlers.youtube.youtube_mp3_extractor.fetch_audio_link",
            AsyncMock(
                return_value=AudioLink(
                    link="https://mirror/a.mp3", duration=source_duration, title="T"
                )
            ),
        ),
        patch(
            "fetcher.handlers.youtube._download_audio",
            AsyncMock(return_value=audio),
        ),
        patch(
            "fetcher.handlers.youtube.whisper_extractor.probe_duration",
            MagicMock(return_value=measured_duration),
        ),
        patch(
            "fetcher.handlers.youtube.whisper_extractor.prepare_chunks",
            MagicMock(return_value=[chunk]),
        ),
        patch(
            "fetcher.handlers.youtube.whisper_extractor.transcribe_chunk_verbose",
            AsyncMock(return_value=[{"start": 0.0, "end": 2.0, "text": "spoken words"}]),
        ),
    )


async def test_transcribed_audio_produces_the_same_chunk_sidecar_as_captions(tmp_path) -> None:
    patches = _patch_pipeline(source_duration=600.0, measured_duration=600.0, tmp_path=tmp_path)
    with _patch_oembed(), patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = await youtube._rapidapi_mp3_tier(_ctx(), _VIDEO_URL)

    assert result.status == 200
    assert result.metadata["chunks"] == [{"text": "spoken words", "start": 0.0, "duration": 2.0}]
    assert "spoken words" in result.content


async def test_tier_rejects_a_download_far_shorter_than_the_source(tmp_path) -> None:
    """A mirror can serve a partial file; transcribing it would store a confident,
    silently incomplete transcript."""
    patches = _patch_pipeline(source_duration=600.0, measured_duration=120.0, tmp_path=tmp_path)
    with _patch_oembed(), patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = await youtube._rapidapi_mp3_tier(_ctx(), _VIDEO_URL)

    assert result.content == ""
    assert "duration" in (result.detail or "").lower()


async def test_audio_tier_sits_last_so_captions_are_always_preferred() -> None:
    """Transcription is slower, paid and less precise than published captions, so
    it must never pre-empt a caption tier."""
    names = [tier.name for tier in youtube.TIERS]

    assert names.index("rapidapi_mp3") > names.index("rapidapi_captions")
    assert names.index("rapidapi_mp3") > names.index("transcript_api")
