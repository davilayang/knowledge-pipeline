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


_PREAMBLE_MARKER = "Markdown Content:"


def strip_preamble(body: str) -> str:
    """Drop Jina Reader's metadata preamble, returning just the article markdown.

    Jina prepends a block of ``Key: value`` lines (always lead with ``Title:``;
    may include ``URL Source:`` / ``Published Time:`` / ``Image:`` / ``Language:``
    in any combination) terminated by a ``Markdown Content:`` line, then the body.
    This anchors on those two landmarks and returns everything after the marker.

    No-op unless the recognizable Jina preamble is present, so output from the
    other tiers (trafilatura / Tavily / RapidAPI), which carry no preamble, passes
    through unchanged. Call only on the success path: ``wraps_upstream_error`` must
    run on the raw body first, since the upstream-error marker lives in the
    preamble region this strips."""
    if not body.lstrip().startswith("Title:"):
        return body
    idx = body.find(_PREAMBLE_MARKER)
    if idx == -1:
        return body
    return body[idx + len(_PREAMBLE_MARKER) :].lstrip("\n")
