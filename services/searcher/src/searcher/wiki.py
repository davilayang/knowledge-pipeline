"""The wiki lane: entity pages found through Chroma's `wiki` collection and resolved
through kp's `_index/resolve.json` and the rendered page files."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

import yaml
from domains.wiki.io import read_meta
from retrievers.vector_store.chroma import COLLECTION_WIKI

from searcher.engine import Hit, LaneFn, dense, lane, timed
from searcher.models import Item, SearchRequest

# resolve.json schema versions this lane reads. kp writes 1; 2 drops the legacy
# `has_derived` key, and `has_user_claims` is read first, so 2 parses unchanged.
SUPPORTED_SCHEMA = {1, 2}


def wiki_lane(chroma: Callable[[], Awaitable[Any]], wiki_dir: Path) -> LaneFn:
    def resolve(entity_ids: list[str]) -> dict[str, Page]:
        # Blocking file reads on the event loop: one resolve.json, plus one small
        # page file per entity found.
        with timed("resolve.json"):
            entities = _load_entities(wiki_dir)
        with timed("wiki pages"):
            pages = {e: _page(wiki_dir, entities, e) for e in entity_ids}
        return {e: p for e, p in pages.items() if p is not None}

    # The producer chunks a summary like any text, so one long summary can come
    # back as several hits: the lane folds them into one item, each chunk a passage.
    return lane(
        "wiki",
        {"dense": dense(chroma, COLLECTION_WIKI, lambda m: str(m.get("content_id", "")))},
        resolve,
        _item,
    )


def _load_entities(wiki_dir: Path) -> dict[str, Any]:
    """resolve.json's entities. A missing, unparseable or unknown-version file fails
    the lane: answering with no pages would read as "nothing matched"."""
    resolve = json.loads((wiki_dir / "_index" / "resolve.json").read_text(encoding="utf-8"))
    if not isinstance(resolve, dict) or resolve.get("schema_version") not in SUPPORTED_SCHEMA:
        raise ValueError("resolve.json has an unsupported schema_version")
    return resolve.get("entities", {})


class Page(NamedTuple):
    ent: dict[str, Any]  # the entity's resolve.json entry
    page: dict[str, Any]  # the page's frontmatter
    entities: dict[str, Any]  # every resolve.json entry, for `related` titles


def _page(wiki_dir: Path, entities: dict[str, Any], entity_id: str) -> Page | None:
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
    return Page(ent, page, entities)


def _item(req: SearchRequest, entity_id: str, found: Page, hit: Hit) -> Item | None:
    """One wiki item, or None when a page field is malformed: an unresolvable piece
    is dropped, not a failed lane (contract §3). `stale` compares the page with
    the chunk that built the item."""
    ent, page, entities = found
    try:
        return Item(
            uri=f"kos://piece/wiki/{quote(entity_id, safe=':')}",
            kind="wiki",
            title=page.get("title") or ent.get("name") or entity_id,
            author=None,
            date=None,
            url=None,
            summary=str(page.get("summary", "")),
            passages=[hit.chunk],
            meta={
                "num_sources": int(ent.get("num_sources", page.get("num_sources", 0)) or 0),
                "stale": str(hit.meta.get("page_hash", "")) != ent.get("page_hash"),
                # Contract §2: up to 4 related entity titles, as the page lists them.
                "related": [
                    entities[rid]["name"]
                    for rid in page.get("related", [])
                    if isinstance(entities.get(rid), dict) and entities[rid].get("name")
                ][:4],
                # kp writes both spellings at schema 1; `has_derived` is the legacy one.
                "has_user_notes": bool(ent.get("has_user_claims", ent.get("has_derived", False))),
            },
            matched_by=[],
        )
    except (TypeError, ValueError, AttributeError):
        return None
