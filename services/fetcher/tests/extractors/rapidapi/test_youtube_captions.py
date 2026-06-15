"""Tests for the RapidAPI youtube-data16 captions extractor.

Fallback for YouTube when the free transcript_api tier is blocked
(e.g. IP-blocked even via the Tailscale proxy). Returns a chunk list
shape-compatible with `youtube_transcript.chunks_to_markdown`: each
chunk has `text`, `start`, `duration` keys.

The upstream API returns entries with `text` / `duration` / `offset`
(plus `lang` / `wordCount` / `speechRate` we discard). The extractor's
job is to translate `offset` → `start` so the same downstream markdown
formatter works.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.extractors.rapidapi import youtube_captions


def _ok_response(payload: object, *, remaining: str = "42") -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"x-ratelimit-requests-remaining": remaining}
    response.json = MagicMock(return_value=payload)
    return response


async def test_fetch_captions_returns_chunks_with_start_field() -> None:
    """Happy path. Real youtube-data16 fields: text/duration/offset.
    Extractor maps offset→start so chunks_to_markdown (the existing
    youtube_transcript formatter) can consume them unchanged."""
    payload = [
        {
            "text": "♪ We're no strangers to love ♪",
            "duration": 3.24,
            "offset": 18.64,
            "lang": "en",
            "wordCount": 5,
            "speechRate": 93,
        },
        {
            "text": "♪ You know the rules and so do I ♪",
            "duration": 4.32,
            "offset": 22.64,
            "lang": "en",
            "wordCount": 8,
            "speechRate": 112,
        },
    ]
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response(payload))

    chunks = await youtube_captions.fetch_captions(client, video_id="dQw4w9WgXcQ", api_key="secret")

    call = client.get.call_args
    assert call.args[0] == "https://youtube-data16.p.rapidapi.com/captions/dQw4w9WgXcQ"
    assert call.kwargs["params"] == {"lang": "en", "format": "json"}
    headers = call.kwargs["headers"]
    assert headers["x-rapidapi-key"] == "secret"
    assert headers["x-rapidapi-host"] == "youtube-data16.p.rapidapi.com"

    assert chunks == [
        {"text": "♪ We're no strangers to love ♪", "start": 18.64, "duration": 3.24},
        {"text": "♪ You know the rules and so do I ♪", "start": 22.64, "duration": 4.32},
    ]


async def test_fetch_captions_passes_lang_override() -> None:
    """Caller can request a non-default language. Useful when a video has
    English auto-captions disabled but a community-uploaded language is
    available (or when targeting a non-English creator)."""
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response([{"text": "x", "duration": 1, "offset": 0}]))

    await youtube_captions.fetch_captions(client, video_id="abc", api_key="k", lang="fr")

    call = client.get.call_args
    assert call.kwargs["params"]["lang"] == "fr"


async def test_fetch_captions_raises_on_http_error() -> None:
    response = MagicMock()
    response.status_code = 403
    response.headers = {}
    response.text = '{"error":"forbidden"}'
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with pytest.raises(ValueError, match="403"):
        await youtube_captions.fetch_captions(client, video_id="abc", api_key="bad")


async def test_fetch_captions_raises_on_quota_exhausted() -> None:
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response([], remaining="0"))

    with pytest.raises(ValueError, match="quota exhausted"):
        await youtube_captions.fetch_captions(client, video_id="abc", api_key="k")


async def test_fetch_captions_raises_on_empty_list() -> None:
    """Empty list = upstream knows the video has no captions in this
    language. Bubble up as ValueError so the handler maps to tier_log
    detail instead of returning empty content the cascade can't
    distinguish from a network failure."""
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response([]))

    with pytest.raises(ValueError, match="empty"):
        await youtube_captions.fetch_captions(client, video_id="abc", api_key="k")


async def test_fetch_captions_raises_on_unexpected_shape() -> None:
    """Object instead of list = upstream changed the response shape.
    Fail loudly so we notice on the next call, not silently downstream."""
    client = MagicMock()
    client.get = AsyncMock(return_value=_ok_response({"error": "bad request"}))

    with pytest.raises(ValueError, match="unexpected response"):
        await youtube_captions.fetch_captions(client, video_id="abc", api_key="k")
