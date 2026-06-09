"""YouTube oEmbed metadata to markdown header."""

import logging
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)


_OEMBED_URL = "https://www.youtube.com/oembed?url={url}&format=json"


async def youtube_metadata_header(client: httpx.AsyncClient, video_url: str) -> str:
    """Fetch oEmbed metadata and format it as a markdown header."""
    try:
        response = await client.get(_OEMBED_URL.format(url=quote(video_url, safe="")))
        if response.status_code != 200:
            raise ValueError(f"oEmbed HTTP {response.status_code}")
        data = response.json()
        title = data.get("title", "Untitled")
        author = data.get("author_name", "Unknown")
        return f"# {title}\n\n**Channel:** {author}\n**Source:** {video_url}\n\n---\n\n"
    except Exception as exc:
        logger.warning("oEmbed fetch failed for %s: %s", video_url, exc)
        return f"# Untitled\n\n**Source:** {video_url}\n\n---\n\n"
