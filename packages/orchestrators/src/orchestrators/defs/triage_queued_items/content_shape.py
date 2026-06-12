"""Rules-only content_shape classifier.

Returns one of:
- `conference_talk` — YouTube channels publishing talks (NeurIPS, AI Engineer, ...)
- `podcast_episode` — audio-URL or YouTube channels publishing podcasts
- `tutorial` — YouTube tutorial channels or tutorial-leaning article hosts
- `opinion_essay` — Substacks, personal blogs, news (folded in for v1)
- `research_summary` — arXiv URLs, research-blog hosts (Google Research, ...)
- `unknown` — fallback when no rule matches

Priority (first match wins):
1. `arXiv` content_type → `research_summary`.
2. URL ends in audio suffix → `podcast_episode`.
3. YouTube channel match against per-shape lists.
4. Article URL host match against `article_host_rules.yaml`.
5. Fallback → `unknown`.

Drives the extractor's per-shape prompt routing (Phase 5). YAML seed values
came from a 200-item Phase 0 corpus analysis; refresh quarterly per the
discovery-pass runbook documented in the README.
"""

from pathlib import Path
from urllib.parse import urlparse

import yaml

from .classify import _AUDIO_SUFFIXES, CONTENT_TYPE_ARXIV
from .enrich import EnrichmentSignals

SHAPE_CONFERENCE_TALK = "conference_talk"
SHAPE_PODCAST_EPISODE = "podcast_episode"
SHAPE_TUTORIAL = "tutorial"
SHAPE_OPINION_ESSAY = "opinion_essay"
SHAPE_RESEARCH_SUMMARY = "research_summary"
SHAPE_UNKNOWN = "unknown"

ALL_CONTENT_SHAPES = {
    SHAPE_CONFERENCE_TALK,
    SHAPE_PODCAST_EPISODE,
    SHAPE_TUTORIAL,
    SHAPE_OPINION_ESSAY,
    SHAPE_RESEARCH_SUMMARY,
    SHAPE_UNKNOWN,
}

_RULES_DIR = Path(__file__).parent


def _load_yaml(name: str) -> dict:
    with (_RULES_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


_CONFERENCE_CHANNELS: frozenset[str] = frozenset(
    _load_yaml("conference_channels.yaml").get("conference_channels", [])
)

_YT_RULES = _load_yaml("youtube_channel_rules.yaml")
_TUTORIAL_CHANNELS: frozenset[str] = frozenset(_YT_RULES.get("tutorial_channels", []))
_PODCAST_CHANNELS: frozenset[str] = frozenset(_YT_RULES.get("podcast_channels", []))
_OPINION_CHANNELS: frozenset[str] = frozenset(_YT_RULES.get("opinion_channels", []))

_ARTICLE_HOST_SHAPE: dict[str, str] = _load_yaml("article_host_rules.yaml").get(
    "article_host_shape", {}
)


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def classify_content_shape(
    *,
    enrichment: EnrichmentSignals,
    content_type: str,
    url: str,
) -> str:
    """Apply rules in priority order. See module docstring for rule list."""
    # 1. arXiv → research_summary (URL-only; enrichment optional).
    if content_type == CONTENT_TYPE_ARXIV:
        return SHAPE_RESEARCH_SUMMARY

    # 2. Audio URL → podcast_episode (URL suffix is authoritative — beats
    # article host rules that may incidentally serve audio).
    path = (urlparse(url).path or "").lower()
    if path.endswith(_AUDIO_SUFFIXES):
        return SHAPE_PODCAST_EPISODE

    # 3. YouTube channel match.
    if enrichment.youtube is not None and enrichment.youtube.channel:
        channel = enrichment.youtube.channel
        if channel in _CONFERENCE_CHANNELS:
            return SHAPE_CONFERENCE_TALK
        if channel in _TUTORIAL_CHANNELS:
            return SHAPE_TUTORIAL
        if channel in _PODCAST_CHANNELS:
            return SHAPE_PODCAST_EPISODE
        if channel in _OPINION_CHANNELS:
            return SHAPE_OPINION_ESSAY

    # 4. Article host rule.
    shape = _ARTICLE_HOST_SHAPE.get(_host_of(url))
    if shape in ALL_CONTENT_SHAPES:
        return shape

    # 5. Fallback.
    return SHAPE_UNKNOWN
