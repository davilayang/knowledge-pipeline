"""YouTube audio-link fetcher via RapidAPI's youtube-mp36.

Obtains a downloadable MP3 link for videos no caption tier can serve;
transcription is the caller's job.

Docs / playground:
https://rapidapi.com/ytjar/api/youtube-mp36/playground/

`/dl` is asynchronous: an unconverted video answers `{"status": "processing",
"progress": N}` until it flips to `{"status": "ok", "link": ...}`. Cold-start
is ~10s. The link is served from a third-party mirror rather than googlevideo,
so it downloads from any IP — no residential egress needed.
"""

import asyncio
import time
from dataclasses import dataclass

import httpx

from fetcher.extractors.rapidapi._client import (
    build_headers,
    check_quota,
    raise_for_status_with_body,
)


_BASE = "https://youtube-mp36.p.rapidapi.com/dl"
_HOST = "youtube-mp36.p.rapidapi.com"
_LABEL = "RapidAPI youtube-mp36"


@dataclass(frozen=True)
class AudioLink:
    """A downloadable MP3 plus the provider's own view of the source."""

    link: str
    duration: float
    title: str


async def fetch_audio_link(
    client: httpx.AsyncClient,
    *,
    video_id: str,
    api_key: str,
    poll_interval_s: float = 3.0,
    poll_budget_s: float = 60.0,
) -> AudioLink:
    """Poll `/dl` until the conversion is ready and return the audio link.

    Raises ValueError on HTTP >=400, quota exhaustion, provider failure, a
    malformed payload, or still-processing at `poll_budget_s` — the handler
    maps each to `RawTierResult.detail`.
    """
    deadline = time.monotonic() + poll_budget_s
    while True:
        response = await client.get(
            _BASE,
            params={"id": video_id},
            headers=build_headers(_HOST, api_key),
        )
        raise_for_status_with_body(response, _LABEL)
        check_quota(response, _LABEL)
        payload = response.json()
        status = payload.get("status")

        if status == "ok":
            link = payload.get("link")
            if not link:
                raise ValueError(f"{_LABEL}: status ok but no link for video_id={video_id}")
            return AudioLink(
                link=link,
                duration=float(payload.get("duration") or 0.0),
                title=payload.get("title") or "",
            )

        if status != "processing":
            raise ValueError(
                f"{_LABEL}: conversion failed for video_id={video_id}: "
                f"status={status!r} msg={payload.get('msg')!r}"
            )

        if time.monotonic() >= deadline:
            raise ValueError(
                f"{_LABEL}: still processing after {poll_budget_s:g}s "
                f"(progress={payload.get('progress')}) for video_id={video_id}"
            )
        await asyncio.sleep(poll_interval_s)
