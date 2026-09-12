"""Verbose (timestamped) transcription is a separate entry point from the plain
text one, so the existing `transcribe_chunk` keeps its `str` return for
`handlers/file_audio.py` rather than becoming a polymorphic union.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx


def _chain():
    from fetcher.extractors import whisper

    return [
        whisper.WhisperChainEntry(
            provider="groq",
            model="whisper-large-v3-turbo",
            base_url="https://api.groq.com/openai/v1",
        )
    ]


async def test_verbose_transcription_requests_segment_timestamps(tmp_path: Path) -> None:
    """Pins the wire contract: a default change on either side (text instead of
    verbose_json, or word-level granularity) would silently reshape the payload."""
    from fetcher.extractors import whisper

    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"fake-audio-bytes")

    sent = {}

    async def fake_post(url, **kwargs):
        sent.update(kwargs["data"])
        return httpx.Response(
            status_code=200,
            text=json.dumps(
                {"duration": 12.0, "segments": [{"start": 0.0, "end": 2.0, "text": "hi"}]}
            ),
        )

    ctx = MagicMock()
    ctx.groq_api_key = "groq-sk"
    ctx.http_client = MagicMock()
    ctx.http_client.post = AsyncMock(side_effect=fake_post)

    segments = await whisper.transcribe_chunk_verbose(ctx, chunk, chain=_chain())

    assert sent["response_format"] == "verbose_json"
    assert json.loads(sent["timestamp_granularities"]) == ["segment"]
    assert segments == [{"start": 0.0, "end": 2.0, "text": "hi"}]
