"""Facebook post fetcher via RapidAPI's facebook-scraper3.

Pfbid-keyed (`GET /post?post_id=<pfbid>`). Fallback tier — used when
facebook-scraper-api4 fails or is quota-exhausted.
Docs: https://rapidapi.com/krasnoludkolo/api/facebook-scraper3/
"""

import re
from datetime import datetime, timezone

import httpx

from fetcher.extractors.rapidapi._client import (
    build_headers,
    check_quota,
    raise_for_status_with_body,
)


_URL = "https://facebook-scraper3.p.rapidapi.com/post"
_HOST = "facebook-scraper3.p.rapidapi.com"
_LABEL = "RapidAPI facebook-scraper3"

_PFBID_RE = re.compile(r"pfbid[0-9a-zA-Z]+")


def extract_pfbid(url: str) -> str | None:
    """Pull the pfbid token from a Facebook post URL, if present."""
    match = _PFBID_RE.search(url)
    return match.group(0) if match else None


def _ts_to_iso(ts: object) -> str:
    """A Unix `timestamp` → UTC ISO string, or "" if absent/out of range."""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OSError, ValueError):
            return ""
    return ""


def _format_post_markdown(results: dict) -> str:
    author = ((results.get("author") or {}).get("name") or "").strip()
    body = (results.get("message") or "").strip()
    post_url = (results.get("url") or "").strip()

    iso = _ts_to_iso(results.get("timestamp"))

    parts: list[str] = []
    if author:
        parts.append(f"**{author}**")
    if iso:
        parts.append(f"_{iso}_")
    if parts:
        parts.append("")
    if body:
        parts.append(body)
    if post_url:
        parts.append("")
        parts.append(f"Source: {post_url}")
    return "\n".join(parts)


async def fetch_post(
    client: httpx.AsyncClient, *, pfbid: str, api_key: str
) -> tuple[str, str, str, str]:
    """Fetch one Facebook post by pfbid; return (markdown, title, author, published).

    `published` is the post `timestamp` as a UTC ISO string from the same response
    (no extra call); `build_metadata` normalizes it to a plain date downstream."""
    response = await client.get(
        _URL, params={"post_id": pfbid}, headers=build_headers(_HOST, api_key)
    )
    raise_for_status_with_body(response, _LABEL)
    check_quota(response, _LABEL)

    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        raise ValueError(f"{_LABEL}: unexpected response shape")

    body = (results.get("message") or "").strip()
    if not body:
        raise ValueError(f"{_LABEL}: empty post body")

    markdown = _format_post_markdown(results)
    title = body.split("\n", 1)[0].strip()[:120] or "Facebook post"
    author = ((results.get("author") or {}).get("name") or "").strip()
    published = _ts_to_iso(results.get("timestamp"))
    return markdown, title, author, published
