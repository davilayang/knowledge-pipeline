# SQLite state tracking for wiki synthesis pipeline.

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_processed (
    item_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    pages_touched TEXT,
    processed_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS wiki_pages (
    entity_id TEXT PRIMARY KEY,
    page_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    related TEXT
);
"""


@dataclass
class ProcessedRecord:
    item_id: str
    source_type: str
    status: str  # "done" | "failed" | "skipped"
    pages_touched: list[str]
    processed_at: datetime
    error: str | None = None


@dataclass
class PageRecord:
    entity_id: str
    page_type: str
    file_path: str
    updated_at: str
    related: list[str]


class WikiStateDB:
    """SQLite state database for tracking wiki processing."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- wiki_processed operations ---

    def get_processed_ids(self, status: str = "done") -> set[str]:
        """Return set of item_ids with the given status."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT item_id FROM wiki_processed WHERE status = ?", (status,)
        ).fetchall()
        return {r["item_id"] for r in rows}

    def mark_processed(
        self,
        item_id: str,
        source_type: str,
        status: str,
        pages_touched: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Insert or update a processed record."""
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO wiki_processed
               (item_id, source_type, status, pages_touched, processed_at, error)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                source_type,
                status,
                json.dumps(pages_touched or []),
                datetime.now().isoformat(),
                error,
            ),
        )
        conn.commit()

    def get_failed(self) -> list[ProcessedRecord]:
        """Return all records with status='failed'."""
        conn = self._connect()
        rows = conn.execute("SELECT * FROM wiki_processed WHERE status = 'failed'").fetchall()
        return [self._row_to_processed(r) for r in rows]

    # --- wiki_pages catalog operations ---

    def upsert_page(
        self,
        entity_id: str,
        page_type: str,
        file_path: str,
        updated_at: str,
        related: list[str] | None = None,
    ) -> None:
        """Insert or update a page catalog entry."""
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO wiki_pages
               (entity_id, page_type, file_path, updated_at, related)
               VALUES (?, ?, ?, ?, ?)""",
            (entity_id, page_type, file_path, updated_at, json.dumps(related or [])),
        )
        conn.commit()

    def get_page(self, entity_id: str) -> PageRecord | None:
        """Get a page catalog entry by entity_id."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM wiki_pages WHERE entity_id = ?", (entity_id,)).fetchone()
        return self._row_to_page(row) if row else None

    def get_all_pages(self) -> list[PageRecord]:
        """Return all page catalog entries."""
        conn = self._connect()
        rows = conn.execute("SELECT * FROM wiki_pages ORDER BY entity_id").fetchall()
        return [self._row_to_page(r) for r in rows]

    # --- internal ---

    @staticmethod
    def _row_to_processed(r: sqlite3.Row) -> ProcessedRecord:
        return ProcessedRecord(
            item_id=r["item_id"],
            source_type=r["source_type"],
            status=r["status"],
            pages_touched=json.loads(r["pages_touched"]) if r["pages_touched"] else [],
            processed_at=datetime.fromisoformat(r["processed_at"]),
            error=r["error"],
        )

    @staticmethod
    def _row_to_page(r: sqlite3.Row) -> PageRecord:
        return PageRecord(
            entity_id=r["entity_id"],
            page_type=r["page_type"],
            file_path=r["file_path"],
            updated_at=r["updated_at"],
            related=json.loads(r["related"]) if r["related"] else [],
        )
