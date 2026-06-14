"""YouTube oEmbed metadata: raw fields + markdown header formatter."""

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)


_OEMBED_URL = "https://www.youtube.com/oembed?url={url}&format=json"


@dataclass(frozen=True)
class YouTubeMetadata:
    title: str | None
    author: str | None
    source_url: str


async def youtube_metadata(client: httpx.AsyncClient, video_url: str) -> YouTubeMetadata:
    """Fetch oEmbed metadata; returns None fields on failure.

    Used both by `youtube_metadata_header` (legacy) and by callers that need
    the title/author separately (e.g. the transcript structurer's hint context).
    """
    try:
        response = await client.get(_OEMBED_URL.format(url=quote(video_url, safe="")))
        if response.status_code != 200:
            raise ValueError(f"oEmbed HTTP {response.status_code}")
        data = response.json()
        return YouTubeMetadata(
            title=data.get("title"),
            author=data.get("author_name"),
            source_url=video_url,
        )
    except Exception as exc:
        logger.warning("oEmbed fetch failed for %s: %s", video_url, exc)
        return YouTubeMetadata(title=None, author=None, source_url=video_url)


async def youtube_metadata_header(client: httpx.AsyncClient, video_url: str) -> str:
    """Fetch oEmbed metadata and format it as a markdown header."""
    meta = await youtube_metadata(client, video_url)
    title = meta.title or "Untitled"
    if meta.author:
        return f"# {title}\n\n**Channel:** {meta.author}\n**Source:** {video_url}\n\n---\n\n"
    return f"# {title}\n\n**Source:** {video_url}\n\n---\n\n"
