"""The wiki lane: entity pages found through Chroma's `wiki` collection and resolved
through kp's `_index/resolve.json` and the rendered page files."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from domains.wiki.io import read_meta
from retrievers.vector_store.chroma import COLLECTION_WIKI

from searcher.engine import LaneFn, add_passage, dense_hits, timed
from searcher.models import Item, MatchedBy, SearchRequest

# resolve.json schema versions this lane reads. kp writes 1; 2 drops the legacy
# `has_derived` key, and `has_user_claims` is read first, so 2 parses unchanged.
SUPPORTED_SCHEMA = {1, 2}


def wiki_lane(chroma: Callable[[], Awaitable[Any]], wiki_dir: Path) -> LaneFn:
    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        hits = await dense_hits(chroma, COLLECTION_WIKI, req, embedding, deadline_at)
        # Blocking file reads on the event loop: one resolve.json, plus one small
        # page file per hit.
        with timed("resolve.json"):
            entities = _load_entities(wiki_dir)
        with timed("wiki pages"):
            pages = [_page(wiki_dir, entities, str(m.get("content_id", ""))) for _, m, _ in hits]
        items: dict[str, Item] = {}
        for (chunk, meta, dist), found in zip(hits, pages, strict=True):
            if found is None:
                continue
            entity_id, ent, page = found
            # The producer chunks a summary like any text, so one long summary can
            # come back as several hits: one item, each chunk a passage.
            if add_passage(items, entity_id, chunk):
                continue
            item = _item(req.query, entity_id, ent, page, entities, chunk, meta, dist, len(items))
            if item is not None:
                items[entity_id] = item
        return list(items.values())

    return lane


def _load_entities(wiki_dir: Path) -> dict[str, Any]:
    """resolve.json's entities. A missing, unparseable or unknown-version file fails
    the lane: answering with no pages would read as "nothing matched"."""
    resolve = json.loads((wiki_dir / "_index" / "resolve.json").read_text(encoding="utf-8"))
    if not isinstance(resolve, dict) or resolve.get("schema_version") not in SUPPORTED_SCHEMA:
        raise ValueError("resolve.json has an unsupported schema_version")
    return resolve.get("entities", {})


def _page(
    wiki_dir: Path, entities: dict[str, Any], entity_id: str
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """The hit's resolve entry and page frontmatter, or None where
    newsletter-assistant's voice recall drops the hit: pruned since indexing, a
    malformed resolve entry, a missing, unparseable or empty page, or a page naming
    another entity."""
    ent = entities.get(entity_id)
    if not isinstance(ent, dict) or not ent.get("file"):
        return None
    try:
        page = read_meta(wiki_dir / ent["file"])
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if not page or str(page.get("entity_id", entity_id)) != entity_id:
        return None
    return entity_id, ent, page


def _item(
    query: str,
    entity_id: str,
    ent: dict[str, Any],
    page: dict[str, Any],
    entities: dict[str, Any],
    chunk: str,
    meta: dict[str, Any],
    dist: float,
    ranked: int,
) -> Item | None:
    """One wiki item, or None when a page field is malformed: an unresolvable piece
    is dropped, not a failed lane (contract §3)."""
    try:
        return Item(
            uri=f"kos://piece/wiki/{quote(entity_id, safe=':')}",
            kind="wiki",
            title=page.get("title") or ent.get("name") or entity_id,
            author=None,
            date=None,
            url=None,
            summary=str(page.get("summary", "")),
            passages=[chunk],
            meta={
                "num_sources": int(ent.get("num_sources", page.get("num_sources", 0)) or 0),
                "stale": str(meta.get("page_hash", "")) != ent.get("page_hash"),
                # Contract §2: up to 4 related entity titles, as the page lists them.
                "related": [
                    entities[rid]["name"]
                    for rid in page.get("related", [])
                    if isinstance(entities.get(rid), dict) and entities[rid].get("name")
                ][:4],
                # kp writes both spellings at schema 1; `has_derived` is the legacy one.
                "has_user_notes": bool(ent.get("has_user_claims", ent.get("has_derived", False))),
            },
            matched_by=[
                MatchedBy(query=query, lane="wiki", method="dense", rank=ranked + 1, score=1 - dist)
            ],
        )
    except (TypeError, ValueError, AttributeError):
        return None
