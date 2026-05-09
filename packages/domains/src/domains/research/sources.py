"""ResearchSource — read research-panel reports from newsletter-assistant's
``research.db``.

Schema lock (newsletter-assistant ``packages/knowledge/src/knowledge/research_store.py``):

    documents(
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        research_session_id  TEXT NOT NULL REFERENCES sessions(...) ON DELETE CASCADE,
        title                TEXT,                      -- nullable
        file_path            TEXT NOT NULL,             -- e.g. data/research_output/xxx.md
        content              TEXT NOT NULL,             -- full markdown body
        created_at           TIMESTAMP NOT NULL         -- ISO 8601 UTC
    )

The ``content`` column stores the full markdown body — the writer commits it
together with the row, so we never need to read the ``.md`` file from disk
(the table comment in research_store.py: "Stores full markdown content so it
survives file deletion"). This intentionally diverges from the original plan
which assumed file-system reads were required; the file_path is retained as
provenance metadata only.

Connection runs in WAL mode (asserted on connect).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from domains.types import IngestItem


class ResearchSource:
    """Yields IngestItems from a newsletter-assistant ``research.db``."""

    def __init__(self, db_path: Path):
        self._db_path = db_path

    def get_item_ids(self) -> list[str]:
        """Document IDs (stringified) ordered by created_at ascending."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM documents ORDER BY created_at, id").fetchall()
        return [str(r["id"]) for r in rows]

    def get_item(self, item_id: str) -> IngestItem | None:
        try:
            doc_id = int(item_id)
        except ValueError:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, file_path, content, created_at FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
        if row is None:
            return None
        return _to_item(row)

    def get_items(self) -> list[IngestItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, file_path, content, created_at "
                "FROM documents ORDER BY created_at, id"
            ).fetchall()
        return [_to_item(r) for r in rows]

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(
                    f"research.db at {self._db_path} is not in WAL mode "
                    f"(journal_mode={mode!r}); concurrent reads would block writers."
                )
            yield conn
        finally:
            conn.close()


def _to_item(row: sqlite3.Row) -> IngestItem:
    created_at = datetime.fromisoformat(row["created_at"])
    doc_id = str(row["id"])
    title = row["title"] or Path(row["file_path"]).stem.replace("_", " ")
    return IngestItem(
        item_id=doc_id,
        title=title,
        date=created_at.date(),
        text=row["content"],
        source_type="research",
        source_ref=f"research:{doc_id}:{row['file_path']}",
    )
