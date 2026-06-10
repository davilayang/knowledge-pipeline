"""Markdown via Jina Reader (https://r.jina.ai/<encoded-url>)."""

from urllib.parse import quote

import httpx


_JINA_BASE = "https://r.jina.ai/"
_REQUEST_HEADERS = {"X-Return-Format": "markdown", "X-Timeout": "20"}


async def fetch(client: httpx.AsyncClient, url: str) -> tuple[str, int]:
    """Fetch a URL through Jina Reader. Returns (body, status_code)."""
    response = await client.get(_JINA_BASE + quote(url, safe=""), headers=_REQUEST_HEADERS)
    return response.text or "", response.status_code


def wraps_upstream_error(body: str) -> bool:
    """Jina returns HTTP 200 even when the upstream 4xx/5xx'd; detect the marker."""
    return "Warning: Target URL returned error" in body
