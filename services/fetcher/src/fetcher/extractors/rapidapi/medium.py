"""Markdown via RapidAPI's Medium2 (mediumapi.com) paywall-bypass API."""

import httpx

from fetcher.extractors.rapidapi._client import build_headers, raise_for_status_with_body


_BASE = "https://medium2.p.rapidapi.com"
_HOST = "medium2.p.rapidapi.com"
_LABEL = "RapidAPI Medium"


async def fetch_markdown(client: httpx.AsyncClient, *, article_id: str, api_key: str) -> str:
    """Fetch a Medium article's markdown via RapidAPI. Raises on HTTP ≥400."""
    response = await client.get(
        f"{_BASE}/article/{article_id}/markdown",
        headers=build_headers(_HOST, api_key),
    )
    raise_for_status_with_body(response, _LABEL)
    return (response.json() or {}).get("markdown", "") or ""
