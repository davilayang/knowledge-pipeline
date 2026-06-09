"""Markdown via the Tavily Extract API."""

import httpx


_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


async def extract(client: httpx.AsyncClient, *, url: str, api_key: str) -> str:
    """Extract a URL via Tavily. Empty string on no-results; raises on HTTP ≥400."""
    response = await client.post(
        _TAVILY_EXTRACT_URL,
        json={"urls": [url]},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.status_code >= 400:
        raise ValueError(f"Tavily extract HTTP {response.status_code}: {response.text[:200]}")
    results = (response.json() or {}).get("results") or []
    if not results:
        return ""
    return results[0].get("raw_content", "") or ""
