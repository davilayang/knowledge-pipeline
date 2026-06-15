"""Facebook post fetcher via RapidAPI's facebook-scraper-api4.

URL-keyed (`GET /get_facebook_post_details?link=<full URL>`). Primary FB tier.
Docs: https://rapidapi.com/oussemaf/api/facebook-scraper-api4/
"""

import httpx

from fetcher.extractors.rapidapi._client import (
    build_headers,
    check_quota,
    raise_for_status_with_body,
)


_URL = "https://facebook-scraper-api4.p.rapidapi.com/get_facebook_post_details"
_HOST = "facebook-scraper-api4.p.rapidapi.com"
_LABEL = "RapidAPI facebook-scraper-api4"


def _extract_post(payload: object) -> dict | None:
    """Pull the first post object out of the API response.

    api4 returns a list with one entry on success; older shapes returned
    a bare dict, so we tolerate both.
    """
    if isinstance(payload, list) and payload:
        first = payload[0]
        return first if isinstance(first, dict) else None
    if isinstance(payload, dict):
        return payload
    return None


def _format_post_markdown(post: dict) -> str:
    """Render the post as markdown — author/time header, body, source footer."""
    values = post.get("values") or {}
    details = post.get("details") or {}
    shared = values.get("shared_post_details") or {}

    body = (values.get("text") or "").strip()
    author = (shared.get("name") or "").strip()
    publish_time = (values.get("publish_time") or "").strip()
    post_link = (details.get("post_link") or "").strip()

    parts: list[str] = []
    if author:
        parts.append(f"**{author}**")
    if publish_time:
        parts.append(f"_{publish_time}_")
    if parts:
        parts.append("")
    if body:
        parts.append(body)
    if post_link:
        parts.append("")
        parts.append(f"Source: {post_link}")
    return "\n".join(parts)


async def fetch_post(client: httpx.AsyncClient, *, url: str, api_key: str) -> tuple[str, str, str]:
    """Fetch one Facebook post; return (markdown, title, author).

    Raises ValueError on HTTP ≥400, quota exhausted, unexpected payload,
    or empty body — handler maps to `RawTierResult.detail`.
    """
    response = await client.get(_URL, params={"link": url}, headers=build_headers(_HOST, api_key))
    raise_for_status_with_body(response, _LABEL)
    check_quota(response, _LABEL)

    post = _extract_post(response.json())
    if post is None:
        raise ValueError(f"{_LABEL}: unexpected response shape")

    markdown = _format_post_markdown(post)
    body = ((post.get("values") or {}).get("text") or "").strip()
    if not body:
        raise ValueError(f"{_LABEL}: empty post body")

    title = body.split("\n", 1)[0].strip()[:120] or "Facebook post"
    author = (
        ((post.get("values") or {}).get("shared_post_details") or {}).get("name") or ""
    ).strip()
    return markdown, title, author
