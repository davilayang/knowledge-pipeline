"""YouTube captions fetcher via RapidAPI's youtube-data16.

Paid fallback for the YouTube handler — fires after the free
`transcript_api` tier (which goes via SOCKS5 to a residential IP) when
that still returns no transcript, typically because YouTube IP-blocks
even the proxied request or because no community transcript is
indexed under the requested language.

Docs / playground:
https://rapidapi.com/poix-poix-default/api/youtube-data16/playground/

Upstream returns a flat JSON list. Each entry:
    {text, duration, offset, lang, wordCount, speechRate}

We map `offset` → `start` so the existing
`youtube_transcript.chunks_to_markdown` formatter consumes the chunks
unchanged. `lang` / `wordCount` / `speechRate` are dropped — not used
downstream and would only widen the surface that the structurer + the
markdown formatter would need to handle.
"""

import httpx

from fetcher.extractors.rapidapi._client import (
    build_headers,
    check_quota,
    raise_for_status_with_body,
)


_BASE = "https://youtube-data16.p.rapidapi.com/captions"
_HOST = "youtube-data16.p.rapidapi.com"
_LABEL = "RapidAPI youtube-data16"


async def fetch_captions(
    client: httpx.AsyncClient,
    *,
    video_id: str,
    api_key: str,
    lang: str = "en",
) -> list[dict]:
    """Fetch caption chunks for ``video_id``. Returns chunks in the shape
    consumed by ``youtube_transcript.chunks_to_markdown``:
    ``[{text, start, duration}, ...]``.

    Raises ValueError on HTTP ≥400, quota exhausted, unexpected payload,
    or an empty caption list — handler maps to ``RawTierResult.detail``.
    """
    response = await client.get(
        f"{_BASE}/{video_id}",
        params={"lang": lang, "format": "json"},
        headers=build_headers(_HOST, api_key),
    )
    raise_for_status_with_body(response, _LABEL)
    check_quota(response, _LABEL)

    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"{_LABEL}: unexpected response shape (expected list)")
    if not payload:
        raise ValueError(f"{_LABEL}: empty caption list for video_id={video_id} lang={lang}")

    return [
        {
            "text": entry.get("text", ""),
            "start": entry.get("offset", 0.0),
            "duration": entry.get("duration", 0.0),
        }
        for entry in payload
    ]
