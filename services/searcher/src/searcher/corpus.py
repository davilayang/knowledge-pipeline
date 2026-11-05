"""The corpus lane: saved items in newsletter-assistant's corpus.db, found through
Chroma's `contents` collection and resolved in corpus.db."""

import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.parse import quote

from retrievers.vector_store.chroma import COLLECTION_NAME

from searcher.engine import K_DEFAULT, LaneFn, time_limit
from searcher.models import Item, MatchedBy, SearchRequest


def corpus_lane(chroma: Callable[[], Awaitable[Any]], db_path: Path) -> LaneFn:
    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        async with time_limit(deadline_at):
            client = await chroma()
            # get_collection, never get_or_create: a search creates nothing.
            collection = await client.get_collection(COLLECTION_NAME, embedding_function=None)
            res = await collection.query(
                query_embeddings=[embedding],
                n_results=req.k or K_DEFAULT,
                include=["documents", "metadatas", "distances"],
            )
        hits = list(zip(res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True))
        rows = _resolve(db_path, sorted({str(m.get("content_id", "")) for _, m, _ in hits}))
        items: dict[str, Item] = {}
        for chunk, meta, dist in hits:
            cid = str(meta.get("content_id", ""))
            if cid in items:
                if chunk not in items[cid].passages:
                    items[cid].passages.append(chunk)
                continue
            row = rows.get(cid)
            if row is None:
                continue
            item = _item(req, cid, chunk, meta, dist, row, rank=len(items) + 1)
            # stored_at is NOT NULL, so no corpus item is undated.
            if req.since is None or (item.date is not None and item.date >= req.since):
                items[cid] = item
        return list(items.values())

    return lane


def _resolve(db_path: Path, content_ids: list[str]) -> dict[str, sqlite3.Row]:
    """Rows by content_id, in one query. newsletter-assistant keeps writing this
    database, so it is opened read-only through SQLite (never `immutable`, which
    would stop SQLite noticing those writes) on a writable directory, where SQLite
    can recreate the `-shm` file a cleanly closed WAL database lacks."""
    # The one blocking call in an otherwise async lane: one batched primary-key
    # query, with no busy wait.
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0)
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" * len(content_ids))
        found = conn.execute(
            f"SELECT content_id, title, author, url, stored_at FROM contents"
            f" WHERE content_id IN ({marks})",
            content_ids,
        ).fetchall()
    finally:
        conn.close()
    return {r["content_id"]: r for r in found}


def _item(
    req: SearchRequest,
    cid: str,
    chunk: str,
    meta: dict[str, Any],
    dist: float,
    row: sqlite3.Row,
    rank: int,
) -> Item:
    # As newsletter-assistant fills them: a chunk carrying a url keeps its own
    # author; one without takes the url, and a missing author, from the row.
    url = meta.get("url") or ""
    author = meta.get("author") or ""
    if not url:
        url = row["url"] or ""
        author = author or row["author"] or ""
    return Item(
        uri=f"kos://piece/corpus/{quote(cid, safe=':')}",
        kind="corpus",
        # The contract never returns a blank title: it becomes the url.
        title=meta.get("title") or row["title"] or url or cid,
        author=author or None,
        date=datetime.fromisoformat(row["stored_at"]).date(),
        url=url or None,
        summary=None,
        passages=[chunk],
        meta={},
        matched_by=[
            MatchedBy(query=req.query, lane="corpus", method="dense", rank=rank, score=1 - dist)
        ],
    )
