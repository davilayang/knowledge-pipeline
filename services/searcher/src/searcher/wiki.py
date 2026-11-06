"""The wiki lane: entity pages found through Chroma's `wiki` collection and resolved
through kp's `_index/resolve.json` and the rendered page files."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from domains.wiki.io import read_meta

from searcher.engine import K_DEFAULT, LaneFn, time_limit, timed
from searcher.models import Item, MatchedBy, SearchRequest

# The collection populate_vector_store writes wiki summaries to.
COLLECTION_WIKI = "wiki"
# resolve.json schema versions this lane reads: kp writes 1; 2 is the version that
# drops the legacy `has_derived` key, which newsletter-assistant already accepts.
SUPPORTED_SCHEMA = {1, 2}


def wiki_lane(chroma: Callable[[], Awaitable[Any]], wiki_dir: Path) -> LaneFn:
    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        async with time_limit(deadline_at):
            client = await chroma()
            # get_collection, never get_or_create: a search creates nothing.
            collection = await client.get_collection(COLLECTION_WIKI, embedding_function=None)
            res = await collection.query(
                query_embeddings=[embedding],
                n_results=req.k or K_DEFAULT,
                include=["documents", "metadatas", "distances"],
            )
        hits = list(zip(res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True))
        # resolve.json once and one small page file per hit: the documented
        # exception to an async-only lane.
        with timed("resolve.json"):
            entities = _load_entities(wiki_dir)
        with timed("wiki pages"):
            pages = [_page(wiki_dir, entities, str(m.get("content_id", ""))) for _, m, _ in hits]
        items: list[Item] = []
        for (chunk, meta, dist), found in zip(hits, pages, strict=True):
            if found is None:
                continue
            entity_id, ent, page = found
            items.append(
                Item(
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
                            if entities.get(rid, {}).get("name")
                        ][:4],
                        # kp writes both spellings at schema 1; `has_derived` is the legacy one.
                        "has_user_notes": bool(
                            ent.get("has_user_claims", ent.get("has_derived", False))
                        ),
                    },
                    matched_by=[
                        MatchedBy(
                            query=req.query,
                            lane="wiki",
                            method="dense",
                            rank=len(items) + 1,
                            score=1 - dist,
                        )
                    ],
                )
            )
        return items

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
    malformed resolve entry, a missing or unparseable page, or a page naming
    another entity."""
    ent = entities.get(entity_id)
    if not isinstance(ent, dict) or not ent.get("file"):
        return None
    try:
        page = read_meta(wiki_dir / ent["file"])
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if str(page.get("entity_id", entity_id)) != entity_id:
        return None
    return entity_id, ent, page
