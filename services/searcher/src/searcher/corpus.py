"""The corpus lane: saved items in newsletter-assistant's corpus.db, found through
Chroma's `contents` collection and resolved in corpus.db."""

import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from retrievers.vector_store.chroma import COLLECTION_CONTENTS

from searcher.engine import Hit, LaneFn, dense, lane, open_ro, timed
from searcher.models import Item, SearchRequest


def corpus_lane(chroma: Callable[[], Awaitable[Any]], db_path: Path) -> LaneFn:
    def resolve(content_ids: list[str]) -> dict[str, sqlite3.Row]:
        with timed("corpus.db"):
            return _resolve(db_path, content_ids)

    return lane(
        "corpus",
        {"dense": dense(chroma, COLLECTION_CONTENTS, lambda m: str(m.get("content_id", "")))},
        resolve,
        _item,
    )


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


def _item(req: SearchRequest, content_id: str, row: sqlite3.Row, hit: Hit) -> Item | None:
    # stored_at is NOT NULL, so every corpus item has a saved date.
    saved = datetime.fromisoformat(row["stored_at"]).date()
    if req.since is not None and saved < req.since:
        return None
    # As newsletter-assistant fills them: a chunk carrying a url keeps its own
    # author; one without takes the url, and a missing author, from the row.
    url = hit.meta.get("url") or ""
    author = hit.meta.get("author") or ""
    if not url:
        url = row["url"] or ""
        author = author or row["author"] or ""
    return Item(
        uri=f"kos://piece/corpus/{quote(content_id, safe=':')}",
        kind="corpus",
        # Contract §2: a title is never empty; a blank one becomes the url, and
        # the content id only where there is no url either.
        title=hit.meta.get("title") or row["title"] or url or content_id,
        author=author or None,
        date=saved,
        url=url or None,
        summary=None,
        passages=[hit.chunk],
        meta={},
        matched_by=[],
    )
