"""The corpus lane: saved items in newsletter-assistant's corpus.db, found through
Chroma's `contents` collection and resolved in corpus.db."""

import sqlite3
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from retrievers.vector_store.chroma import COLLECTION_CONTENTS

from searcher.engine import LaneFn, add_passage, dense_hits, open_ro, timed
from searcher.models import Item, MatchedBy, SearchRequest


def corpus_lane(chroma: Callable[[], Awaitable[Any]], db_path: Path) -> LaneFn:
    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        hits = await dense_hits(chroma, COLLECTION_CONTENTS, req, embedding, deadline_at)
        with timed("corpus.db"):
            rows = _resolve(db_path, sorted({str(m.get("content_id", "")) for _, m, _ in hits}))
        items: dict[str, Item] = {}
        for chunk, meta, dist in hits:
            content_id = str(meta.get("content_id", ""))
            if add_passage(items, content_id, chunk):
                continue
            row = rows.get(content_id)
            if row is None:
                continue
            # stored_at is NOT NULL, so every corpus item has a saved date.
            saved = datetime.fromisoformat(row["stored_at"]).date()
            if req.since is None or saved >= req.since:
                items[content_id] = _item(
                    req.query, content_id, chunk, meta, dist, row, saved, rank=len(items) + 1
                )
        return list(items.values())

    return lane


def _resolve(db_path: Path, content_ids: list[str]) -> dict[str, sqlite3.Row]:
    """Rows by content_id, in one query: the one blocking call in an otherwise async
    lane."""
    conn = open_ro(db_path)
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
    query: str,
    content_id: str,
    chunk: str,
    meta: dict[str, Any],
    dist: float,
    row: sqlite3.Row,
    saved: date,
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
        uri=f"kos://piece/corpus/{quote(content_id, safe=':')}",
        kind="corpus",
        # Contract §2: a title is never empty; a blank one becomes the url, and
        # the content id only where there is no url either.
        title=meta.get("title") or row["title"] or url or content_id,
        author=author or None,
        date=saved,
        url=url or None,
        summary=None,
        passages=[chunk],
        meta={},
        matched_by=[
            MatchedBy(query=query, lane="corpus", method="dense", rank=rank, score=1 - dist)
        ],
    )
