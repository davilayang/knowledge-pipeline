"""Read-only SQLite access to raw_store.db.

Boundary rule: this module has no Dagster, LLM, or ML deps. Callers must
pass `db_path` explicitly — the path is owned by the orchestrators package.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass
class ContentRow:
    content_id: str
    newsletter_id: int
    source_key: str
    content_date: date | None
    title: str
    author: str
    url: str | None
    content_md: str
    scrape_status: str = "full"
    fetch_tier: str | None = None
    fetch_attempts: int = 0
    vector_status: str = "pending"
    stored_at: datetime | None = None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _row_to_content(r: sqlite3.Row) -> ContentRow:
    return ContentRow(
        content_id=r["content_id"],
        newsletter_id=r["newsletter_id"],
        source_key=r["source_key"],
        content_date=date.fromisoformat(r["content_date"]) if r["content_date"] else None,
        title=r["title"] or "",
        author=r["author"] or "",
        url=r["url"],
        content_md=r["content_md"] or "",
        scrape_status=r["scrape_status"] or "full",
        fetch_tier=r["fetch_tier"],
        fetch_attempts=r["fetch_attempts"] or 0,
        vector_status=r["vector_status"] or "pending",
        stored_at=datetime.fromisoformat(r["stored_at"]) if r["stored_at"] else None,
    )


def get_contents(
    *,
    db_path: Path,
    source_key: str | None = None,
    since: date | None = None,
    vector_status: str | None = None,
) -> list[ContentRow]:
    clauses: list[str] = []
    params: list[str] = []
    if source_key is not None:
        clauses.append("source_key = ?")
        params.append(source_key)
    if since is not None:
        clauses.append("COALESCE(content_date, DATE(stored_at)) >= ?")
        params.append(since.isoformat())
    if vector_status is not None:
        clauses.append("vector_status = ?")
        params.append(vector_status)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM contents{where} ORDER BY stored_at",
            params,
        ).fetchall()
    return [_row_to_content(r) for r in rows]


def get_content_ids(*, db_path: Path) -> list[str]:
    """Return all content_ids ordered by stored_at — IDs only, no payload.

    Used by discover_pending_contents to enumerate raw_store cheaply (full
    content_md is large). For batch reads of full rows use get_contents().
    """
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT content_id FROM contents ORDER BY stored_at").fetchall()
    return [r["content_id"] for r in rows]


def get_content_by_id(content_id: str, *, db_path: Path) -> ContentRow | None:
    """Return one ContentRow by content_id, or None if absent.

    Used by the dynamic-partitioned synthesize_item asset to load just the
    one article for a given partition_key. Bulk get_contents() would also
    work but reads every row; this is the focused single-item lookup.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM contents WHERE content_id = ?",
            (content_id,),
        ).fetchone()
    return _row_to_content(row) if row else None


def set_vector_status(content_id: str, status: str, *, db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE contents SET vector_status = ? WHERE content_id = ?",
            (status, content_id),
        )


def count_contents(*, db_path: Path) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM contents").fetchone()
    return row[0]
