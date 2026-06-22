"""SQLite helpers for wiki state — pure functions taking a sqlite3 connection.

Single source of truth for the wiki.db tables: processed / pages / aliases /
page_sources. Callers manage connection lifecycle and transactions (`with conn:`
for an atomic block); these helpers issue SQL only.

`connect()` opens a WAL connection with the standard pragmas (mirrors the
sibling stores raw_store.db / queue.db); `create_schema()` applies wiki.sql.
The schema lives in packages/domains/src/domains/wiki/schema/wiki.sql.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from domains.wiki.aliases import AliasEntry, AliasStore
from domains.wiki.types import WikiPage

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "wiki.sql"


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a wiki.db connection with WAL + busy_timeout (sibling-store defaults).

    Default isolation_level is kept (not autocommit) so `with conn:` brackets an
    atomic transaction — the commit path writes pages + aliases + page_sources +
    processed all-or-nothing. row_factory is sqlite3.Row (positional unpacking
    still works, plus name access for ad-hoc reads).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def create_schema(*, db_path: Path | str) -> None:
    """Apply wiki.sql idempotently (all DDL is IF NOT EXISTS)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a wiki.db connection and close it on exit.

    sqlite3's `with conn:` brackets a transaction but does NOT close the
    connection, so callers that want a scoped connection use this. Wrap an
    inner `with conn:` for an atomic write block.
    """
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@dataclass
class ProcessedRecord:
    item_id: str
    source_type: str
    status: str  # 'ok' | 'error' | 'skipped'
    error: str | None
    processed_at: str


@dataclass
class PageRecord:
    entity_id: str
    page_type: str
    file_path: str
    related: list[str]
    sources: list[str]
    source_types: list[str]
    updated_at: str


def _json_list(value: str | None) -> list[str]:
    """Decode a JSON-array TEXT column to a list; tolerate NULL/empty."""
    if not value:
        return []
    return json.loads(value)


# --------------------------------------------------------------------------
# processed
# --------------------------------------------------------------------------


def insert_processed(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source_type: str,
    status: str,
    error: str | None = None,
) -> None:
    """Upsert a processed row. Caller manages transaction."""
    conn.execute(
        """
        INSERT INTO processed (item_id, source_type, status, error, processed_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (item_id, source_type)
        DO UPDATE SET status = excluded.status,
                      error = excluded.error,
                      processed_at = CURRENT_TIMESTAMP
        """,
        (item_id, source_type, status, error),
    )


def get_processed_ids(conn: sqlite3.Connection, status: str = "ok") -> set[str]:
    """Return the set of item_ids with the given status."""
    rows = conn.execute(
        "SELECT item_id FROM processed WHERE status = ?",
        (status,),
    ).fetchall()
    return {r[0] for r in rows}


def get_failed(conn: sqlite3.Connection) -> list[ProcessedRecord]:
    """Return all processed rows with status='error', most recent first."""
    rows = conn.execute(
        """
        SELECT item_id, source_type, status, error, processed_at
        FROM processed
        WHERE status = 'error'
        ORDER BY processed_at DESC
        """
    ).fetchall()
    return [ProcessedRecord(*r) for r in rows]


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------


def upsert_page(
    conn: sqlite3.Connection,
    *,
    page: WikiPage,
    file_path: str,
    source_types: list[str],
) -> None:
    """Upsert a pages row from a WikiPage. Caller manages transaction.

    source_types is the list of source_type strings (e.g. ["raw_store"]) for
    every IngestItem that has contributed to this page across runs.
    """
    conn.execute(
        """
        INSERT INTO pages
            (entity_id, page_type, file_path, related, sources, source_types, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (entity_id)
        DO UPDATE SET page_type = excluded.page_type,
                      file_path = excluded.file_path,
                      related = excluded.related,
                      sources = excluded.sources,
                      source_types = excluded.source_types,
                      updated_at = CURRENT_TIMESTAMP
        """,
        (
            page.entity_id,
            page.page_type,
            file_path,
            json.dumps(page.related),
            json.dumps(page.sources),
            json.dumps(source_types),
        ),
    )


def get_page(conn: sqlite3.Connection, entity_id: str) -> PageRecord | None:
    """Return one page row by entity_id, or None if absent."""
    row = conn.execute(
        """
        SELECT entity_id, page_type, file_path, related, sources, source_types, updated_at
        FROM pages
        WHERE entity_id = ?
        """,
        (entity_id,),
    ).fetchone()
    return _row_to_page(row) if row else None


def get_all_pages(conn: sqlite3.Connection) -> list[PageRecord]:
    """Return every pages row, ordered by entity_id."""
    rows = conn.execute(
        """
        SELECT entity_id, page_type, file_path, related, sources, source_types, updated_at
        FROM pages
        ORDER BY entity_id
        """
    ).fetchall()
    return [_row_to_page(r) for r in rows]


def _row_to_page(row: sqlite3.Row) -> PageRecord:
    entity_id, page_type, file_path, related, sources, source_types, updated_at = row
    return PageRecord(
        entity_id=entity_id,
        page_type=page_type,
        file_path=file_path,
        related=_json_list(related),
        sources=_json_list(sources),
        source_types=_json_list(source_types),
        updated_at=updated_at,
    )


# --------------------------------------------------------------------------
# aliases
# --------------------------------------------------------------------------


def insert_aliases_idempotent(
    conn: sqlite3.Connection,
    entries: list[tuple[str, str, list[str]]],
) -> None:
    """Insert (entity_id, canonical_name, alias) rows; skip on UNIQUE conflict.

    Each entry is (entity_id, canonical_name, list_of_aliases). Each alias —
    plus the canonical name itself — becomes one row. ON CONFLICT DO NOTHING
    handles the case where a concurrent partition already claimed the alias
    for a different entity (first claim sticks).
    """
    rows: list[tuple[str, str, str]] = []
    for entity_id, canonical, aliases in entries:
        seen: set[str] = set()
        for name in [canonical, *aliases]:
            if name and name not in seen:
                seen.add(name)
                rows.append((entity_id, canonical, name))

    if not rows:
        return

    conn.executemany(
        """
        INSERT INTO aliases (entity_id, canonical_name, alias)
        VALUES (?, ?, ?)
        ON CONFLICT (alias) DO NOTHING
        """,
        rows,
    )


def get_aliases_for_entity(conn: sqlite3.Connection, entity_id: str) -> list[str]:
    """Return every alias for `entity_id`, sorted ascending for determinism."""
    rows = conn.execute(
        "SELECT alias FROM aliases WHERE entity_id = ? ORDER BY alias",
        (entity_id,),
    ).fetchall()
    return [r[0] for r in rows]


def insert_page_source(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    item_id: str,
    source_type: str,
) -> None:
    """Record one (entity_id, item_id, source_type) contribution in the
    page_sources ledger. Idempotent (ON CONFLICT DO NOTHING) so retries and
    re-processing don't double-count. Caller manages the transaction."""
    conn.execute(
        """
        INSERT INTO page_sources (entity_id, item_id, source_type)
        VALUES (?, ?, ?)
        ON CONFLICT (entity_id, item_id, source_type) DO NOTHING
        """,
        (entity_id, item_id, source_type),
    )


def count_sources_for_entity(conn: sqlite3.Connection, entity_id: str) -> int:
    """Return the count of distinct item_ids that have contributed to this
    entity, from the page_sources ledger — the deterministic record of which
    content item surfaced which entity, not the LLM-authored pages.sources
    array.
    """
    row = conn.execute(
        "SELECT count(DISTINCT item_id) FROM page_sources WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def is_source_for_entity(conn: sqlite3.Connection, entity_id: str, item_id: str) -> bool:
    """True if item_id is already a recorded contribution for entity_id.

    Lets the producer decide whether the current tick adds a new source (so
    num_sources reflects the post-commit ledger state) without double-counting
    a re-processed item.
    """
    row = conn.execute(
        "SELECT 1 FROM page_sources WHERE entity_id = ? AND item_id = ? LIMIT 1",
        (entity_id, item_id),
    ).fetchone()
    return row is not None


def snapshot_aliases(conn: sqlite3.Connection) -> AliasStore:
    """Read every alias row into an in-memory AliasStore for prompt use."""
    rows = conn.execute(
        "SELECT entity_id, canonical_name, alias FROM aliases ORDER BY entity_id, alias"
    ).fetchall()

    entries: dict[str, AliasEntry] = {}
    for entity_id, canonical, alias in rows:
        if entity_id not in entries:
            entries[entity_id] = AliasEntry(canonical=canonical, aliases=[])
        if alias != canonical:
            entries[entity_id].aliases.append(alias)
    return AliasStore(entries=entries)
