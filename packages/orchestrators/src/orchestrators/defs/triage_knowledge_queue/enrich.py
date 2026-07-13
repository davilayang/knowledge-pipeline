"""URL → enrichment signals for content_shape classification.

Per-source HTTP probes dispatched by `content_type`:

- YouTube → oEmbed (channel + title; no API key)
- arXiv → public Atom API (title + abstract + categories)
- Article → reuses `url_meta.fetch_url_meta` (redirected_url + title + description)
- Podcast / Other → empty signals (HEAD-sniff for podcasts is a follow-up)

Failure-tolerant: any per-source HTTP / parse error collapses to empty
signals for that source. `enrich_url` never raises; triage must not fail
on enrichment. Output is consumed by `classify_content_shape` to drive
the extractor's per-shape prompt selection (conference channels,
tutorial channels, podcast shows, research-blog hosts, etc.).
"""

import json
from dataclasses import asdict, dataclass

import httpx
from defusedxml import ElementTree as ET
from domains.arxiv_urls import extract_arxiv_id

from .classify import (
    ARTICLE_LIKE_TYPES,
    CONTENT_TYPE_ARXIV,
    CONTENT_TYPE_YOUTUBE,
)
from .url_meta import fetch_url_meta

_TIMEOUT_S = 10.0
_OEMBED_URL = "https://www.youtube.com/oembed"
_ARXIV_API = "http://export.arxiv.org/api/query"

_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass(frozen=True)
class YoutubeSignals:
    channel: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class ArxivSignals:
    title: str | None = None
    abstract: str | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArticleSignals:
    redirected_url: str | None = None
    title: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class EnrichmentSignals:
    """Per-source enrichment captured for content_shape classification.

    An "empty" `EnrichmentSignals()` is a valid signal meaning "we tried
    and got nothing", distinct from "we haven't enriched yet" (which is
    `enrichment_json IS NULL` in queue.db). Only populated sub-signals
    serialise — keeps the JSON tight and the classifier's reads narrow.
    """

    youtube: YoutubeSignals | None = None
    arxiv: ArxivSignals | None = None
    article: ArticleSignals | None = None

    def to_json(self) -> str:
        payload: dict[str, dict] = {}
        if self.youtube is not None:
            payload["youtube"] = asdict(self.youtube)
        if self.arxiv is not None:
            payload["arxiv"] = asdict(self.arxiv)
        if self.article is not None:
            payload["article"] = asdict(self.article)
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | None) -> "EnrichmentSignals":
        """Inverse of `to_json`. `None` / empty / malformed input → empty
        signals — same failure-tolerance contract as `enrich_url`."""
        if not raw:
            return cls()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(payload, dict):
            return cls()
        return cls(
            youtube=_build_youtube(payload.get("youtube")),
            arxiv=_build_arxiv(payload.get("arxiv")),
            article=_build_article(payload.get("article")),
        )


def _build_youtube(data: dict | None) -> YoutubeSignals | None:
    if not isinstance(data, dict):
        return None
    return YoutubeSignals(channel=data.get("channel"), title=data.get("title"))


def _build_arxiv(data: dict | None) -> ArxivSignals | None:
    if not isinstance(data, dict):
        return None
    cats = data.get("categories") or []
    return ArxivSignals(
        title=data.get("title"),
        abstract=data.get("abstract"),
        categories=tuple(c for c in cats if isinstance(c, str)),
    )


def _build_article(data: dict | None) -> ArticleSignals | None:
    if not isinstance(data, dict):
        return None
    # Accept the old `final_url` key from rows enriched before the rename so
    # `enrichment_json` payloads written by pre-rename builds still parse.
    redirected_url = data.get("redirected_url") or data.get("final_url")
    return ArticleSignals(
        redirected_url=redirected_url,
        title=data.get("title"),
        description=data.get("description"),
    )


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = " ".join(value.split())
    return stripped or None


def _youtube_signals(url: str, *, timeout: float = _TIMEOUT_S) -> YoutubeSignals:
    try:
        resp = httpx.get(
            _OEMBED_URL,
            params={"url": url, "format": "json"},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            return YoutubeSignals()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return YoutubeSignals()
    return YoutubeSignals(
        channel=_norm(data.get("author_name")),
        title=_norm(data.get("title")),
    )


def _arxiv_signals(url: str, *, timeout: float = _TIMEOUT_S) -> ArxivSignals:
    arxiv_id = extract_arxiv_id(url)
    if arxiv_id is None:
        return ArxivSignals()
    try:
        resp = httpx.get(_ARXIV_API, params={"id_list": arxiv_id}, timeout=timeout)
        if resp.status_code >= 400:
            return ArxivSignals()
        root = ET.fromstring(resp.text)
    except (httpx.HTTPError, ET.ParseError):
        return ArxivSignals()

    entry = root.find("atom:entry", _ATOM_NS)
    if entry is None:
        return ArxivSignals()
    title_el = entry.find("atom:title", _ATOM_NS)
    summary_el = entry.find("atom:summary", _ATOM_NS)
    categories = tuple(
        term for c in entry.findall("atom:category", _ATOM_NS) if (term := c.attrib.get("term"))
    )
    return ArxivSignals(
        title=_norm(title_el.text if title_el is not None else None),
        abstract=_norm(summary_el.text if summary_el is not None else None),
        categories=categories,
    )


def _article_signals(url: str) -> ArticleSignals:
    meta = fetch_url_meta(url)
    return ArticleSignals(
        redirected_url=meta.redirected_url,
        title=meta.title,
        description=meta.description,
    )


def enrich_url(url: str, content_type: str) -> EnrichmentSignals:
    """Dispatch enrichment by `content_type`. Never raises.

    Returns `EnrichmentSignals()` (all-None) for `file_pdf` / `file_audio` content
    types — out of scope here. Any unexpected exception from a per-source
    helper is swallowed and collapses to empty signals so triage stays
    unblocked.
    """
    try:
        if content_type == CONTENT_TYPE_YOUTUBE:
            return EnrichmentSignals(youtube=_youtube_signals(url))
        if content_type == CONTENT_TYPE_ARXIV:
            return EnrichmentSignals(arxiv=_arxiv_signals(url))
        if content_type in ARTICLE_LIKE_TYPES:
            return EnrichmentSignals(article=_article_signals(url))
        return EnrichmentSignals()
    except Exception:
        return EnrichmentSignals()
