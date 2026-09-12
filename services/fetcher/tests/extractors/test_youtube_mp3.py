"""RapidAPI youtube-mp36 audio-link fetcher.

`/dl` is asynchronous: a video the provider has not converted yet answers
`{"status": "processing"}` and must be polled until it flips to `ok`.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _json_response(payload: dict) -> httpx.Response:
    return httpx.Response(status_code=200, json=payload)


async def test_polls_until_the_conversion_is_ready() -> None:
    from fetcher.extractors.rapidapi import youtube_mp3

    payloads = [
        {"status": "processing", "progress": 0},
        {"status": "processing", "progress": 77},
        {"status": "ok", "link": "https://mirror/a.mp3", "duration": 467.58, "title": "A talk"},
    ]
    client = MagicMock()
    client.get = AsyncMock(side_effect=[_json_response(p) for p in payloads])

    result = await youtube_mp3.fetch_audio_link(
        client, video_id="abc12345678", api_key="k", poll_interval_s=0, poll_budget_s=30
    )

    assert result.link == "https://mirror/a.mp3"
    assert result.duration == 467.58
    assert result.title == "A talk"
    assert client.get.await_count == 3


async def test_gives_up_when_still_processing_at_the_budget() -> None:
    """A conversion that never completes must not hold the fetch request open —
    the caller has a shared end-to-end budget to leave room in."""
    from fetcher.extractors.rapidapi import youtube_mp3

    client = MagicMock()
    client.get = AsyncMock(return_value=_json_response({"status": "processing", "progress": 5}))

    with pytest.raises(ValueError, match="still processing"):
        await youtube_mp3.fetch_audio_link(
            client, video_id="abc12345678", api_key="k", poll_interval_s=0, poll_budget_s=0
        )
