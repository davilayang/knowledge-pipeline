"""Orchestrator-side YouTube substitution for podcast queue items.

When a Notion queue item carries an audio MP3 URL whose show is in
`podcast_youtube_map.yaml`, fetch the show's YouTube playlist Atom feed
and fuzzy-match the audio episode title (after stripping the show prefix)
against playlist entry titles. On a confident match, the orchestrator
substitutes the YouTube URL into the canonical_url for that queue row —
the fetcher service then routes the URL via its youtube handler and we
get a free transcript instead of paying for Whisper transcription.

Substring + fuzzy match (no date-proximity heuristic) — see Phase 5 plan
for the decision.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path

import httpx
import yaml

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_YT_NS = "{http://www.youtube.com/xml/schemas/2015}"
_FEED_URL = "https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
_TIMEOUT_S = 10.0
_MAPPINGS_PATH = Path(__file__).parent / "podcast_youtube_map.yaml"


@dataclass(frozen=True)
class PodcastYouTubeMapping:
    name: str
    audio_url_substring: str
    youtube_playlist_id: str
    title_strip_pattern: str
    match_threshold: int


@dataclass(frozen=True)
class FeedEntry:
    video_id: str
    title: str
    url: str


def _load_mappings(path: Path) -> list[PodcastYouTubeMapping]:
    data = yaml.safe_load(path.read_text()) or {}
    return [PodcastYouTubeMapping(**row) for row in data.get("mappings", [])]


@cache
def _default_mappings() -> list[PodcastYouTubeMapping]:
    if not _MAPPINGS_PATH.exists():
        return []
    return _load_mappings(_MAPPINGS_PATH)


def _strip_title(audio_title: str, pattern: str) -> str:
    return re.sub(pattern, "", audio_title)


def maybe_redirect_podcast_to_youtube(
    *,
    audio_url: str,
    audio_title: str,
    mappings: list[PodcastYouTubeMapping] | None = None,
) -> str | None:
    """Look up a YouTube equivalent for a podcast audio URL.

    Find the mapping whose `audio_url_substring` is present in `audio_url`,
    fetch that show's YouTube playlist Atom feed, strip the show prefix
    from `audio_title`, and fuzzy-match the stripped title against entry
    titles. Returns the best-matching entry's URL when its similarity
    ratio is at or above `mapping.match_threshold / 100`, else None.

    `mappings=None` loads the bundled `podcast_youtube_map.yaml`; tests
    pass an explicit list.
    """
    effective_mappings = _default_mappings() if mappings is None else mappings
    for mapping in effective_mappings:
        if mapping.audio_url_substring not in audio_url:
            continue
        entries = _fetch_playlist_feed(mapping.youtube_playlist_id)
        if not entries:
            return None
        needle = _strip_title(audio_title, mapping.title_strip_pattern)
        threshold = mapping.match_threshold / 100
        best: tuple[float, FeedEntry] | None = None
        for entry in entries:
            score = SequenceMatcher(None, needle, entry.title).ratio()
            if best is None or score > best[0]:
                best = (score, entry)
        if best is not None and best[0] >= threshold:
            return best[1].url
        return None
    return None


def _fetch_playlist_feed(playlist_id: str) -> list[FeedEntry]:
    response = httpx.get(_FEED_URL.format(playlist_id=playlist_id), timeout=_TIMEOUT_S)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    entries: list[FeedEntry] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        video_id_el = entry.find(f"{_YT_NS}videoId")
        title_el = entry.find(f"{_ATOM_NS}title")
        link_el = entry.find(f"{_ATOM_NS}link[@rel='alternate']")
        if video_id_el is None or title_el is None or link_el is None:
            continue
        entries.append(
            FeedEntry(
                video_id=video_id_el.text or "",
                title=title_el.text or "",
                url=link_el.get("href") or "",
            )
        )
    return entries
