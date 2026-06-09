"""Markdown via RapidAPI's Medium2 (mediumapi.com) paywall-bypass API."""

import httpx


_BASE = "https://medium2.p.rapidapi.com"
_HOST = "medium2.p.rapidapi.com"


async def fetch_markdown(
    client: httpx.AsyncClient, *, article_id: str, api_key: str
) -> str:
    """Fetch a Medium article's markdown via RapidAPI. Raises on HTTP ≥400."""
    response = await client.get(
        f"{_BASE}/article/{article_id}/markdown",
        headers={"x-rapidapi-key": api_key, "x-rapidapi-host": _HOST},
    )
    if response.status_code >= 400:
        raise ValueError(
            f"RapidAPI Medium HTTP {response.status_code}: {response.text[:200]}"
        )
    return (response.json() or {}).get("markdown", "") or ""
