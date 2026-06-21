"""Postgres helpers for wiki state — pure functions taking a psycopg connection.

Single source of truth for wiki.processed / wiki.pages / wiki.aliases.
Callers manage connection lifecycle and transactions; these helpers
issue SQL only.

Schema lives in packages/domains/src/domains/wiki/schema/wiki.sql and is
applied via `psql -f wiki.sql` (or pytest-postgresql `load=[...]` in tests).
"""

import json
from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection

from domains.wiki.aliases import AliasEntry, AliasStore
from domains.wiki.types import WikiPage


@dataclass
class ProcessedRecord:
    item_id: str
    source_type: str
    status: str  # 'ok' | 'error' | 'skipped'
    error: str | None
    processed_at: datetime


@dataclass
class PageRecord:
    entity_id: str
    page_type: str
    file_path: str
    related: list[str]
    sources: list[str]
    source_types: list[str]
    updated_at: datetime


# --------------------------------------------------------------------------
# wiki.processed
# --------------------------------------------------------------------------


def insert_processed(
    conn: Connection,
    *,
    item_id: str,
    source_type: str,
    status: str,
    error: str | None = None,
) -> None:
    """Upsert a wiki.processed row. Caller manages transaction."""
    conn.execute(
        """
        INSERT INTO wiki.processed (item_id, source_type, status, error, processed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (item_id, source_type)
        DO UPDATE SET status = EXCLUDED.status,
                      error = EXCLUDED.error,
                      processed_at = now()
        """,
        (item_id, source_type, status, error),
    )


def get_processed_ids(conn: Connection, status: str = "ok") -> set[str]:
    """Return the set of item_ids with the given status."""
    rows = conn.execute(
        "SELECT item_id FROM wiki.processed WHERE status = %s",
        (status,),
    ).fetchall()
    return {r[0] for r in rows}


def get_failed(conn: Connection) -> list[ProcessedRecord]:
    """Return all wiki.processed rows with status='error', most recent first."""
    rows = conn.execute(
        """
        SELECT item_id, source_type, status, error, processed_at
        FROM wiki.processed
        WHERE status = 'error'
        ORDER BY processed_at DESC
        """
    ).fetchall()
    return [ProcessedRecord(*r) for r in rows]


# --------------------------------------------------------------------------
# wiki.pages
# --------------------------------------------------------------------------


def upsert_page(
    conn: Connection,
    *,
    page: WikiPage,
    file_path: str,
    source_types: list[str],
) -> None:
    """Upsert a wiki.pages row from a WikiPage. Caller manages transaction.

    source_types is the list of source_type strings (e.g. ["raw_store"]) for
    every IngestItem that has contributed to this page across runs.
    """
    conn.execute(
        """
        INSERT INTO wiki.pages
            (entity_id, page_type, file_path, related, sources, source_types, updated_at)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, now())
        ON CONFLICT (entity_id)
        DO UPDATE SET page_type = EXCLUDED.page_type,
                      file_path = EXCLUDED.file_path,
                      related = EXCLUDED.related,
                      sources = EXCLUDED.sources,
                      source_types = EXCLUDED.source_types,
                      updated_at = now()
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


def get_page(conn: Connection, entity_id: str) -> PageRecord | None:
    """Return one page row by entity_id, or None if absent."""
    row = conn.execute(
        """
        SELECT entity_id, page_type, file_path, related, sources, source_types, updated_at
        FROM wiki.pages
        WHERE entity_id = %s
        """,
        (entity_id,),
    ).fetchone()
    return _row_to_page(row) if row else None


def get_all_pages(conn: Connection) -> list[PageRecord]:
    """Return every wiki.pages row, ordered by entity_id."""
    rows = conn.execute(
        """
        SELECT entity_id, page_type, file_path, related, sources, source_types, updated_at
        FROM wiki.pages
        ORDER BY entity_id
        """
    ).fetchall()
    return [_row_to_page(r) for r in rows]


def _row_to_page(row: tuple) -> PageRecord:
    entity_id, page_type, file_path, related, sources, source_types, updated_at = row
    return PageRecord(
        entity_id=entity_id,
        page_type=page_type,
        file_path=file_path,
        related=related or [],
        sources=sources or [],
        source_types=source_types or [],
        updated_at=updated_at,
    )


# --------------------------------------------------------------------------
# wiki.aliases
# --------------------------------------------------------------------------


def insert_aliases_idempotent(
    conn: Connection,
    entries: list[tuple[str, str, list[str]]],
) -> None:
    """Insert (entity_id, canonical_name, alias) rows; skip on UNIQUE conflict.

    Each entry is (entity_id, canonical_name, list_of_aliases). Each alias —
    plus the canonical name itself — becomes one row. ON CONFLICT DO NOTHING
    handles the case where a concurrent partition already claimed the alias
    for a different entity (last-writer-wins; first claim sticks).
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

    conn.cursor().executemany(
        """
        INSERT INTO wiki.aliases (entity_id, canonical_name, alias)
        VALUES (%s, %s, %s)
        ON CONFLICT (alias) DO NOTHING
        """,
        rows,
    )


def get_aliases_for_entity(conn: Connection, entity_id: str) -> list[str]:
    """Return every alias for `entity_id`, sorted ascending for determinism."""
    rows = conn.execute(
        "SELECT alias FROM wiki.aliases WHERE entity_id = %s ORDER BY alias",
        (entity_id,),
    ).fetchall()
    return [r[0] for r in rows]


def insert_page_source(
    conn: Connection,
    *,
    entity_id: str,
    item_id: str,
    source_type: str,
) -> None:
    """Record one (entity_id, item_id, source_type) contribution in the
    wiki.page_sources ledger. Idempotent (ON CONFLICT DO NOTHING) so retries
    and re-processing don't double-count. Caller manages the transaction."""
    conn.execute(
        """
        INSERT INTO wiki.page_sources (entity_id, item_id, source_type)
        VALUES (%s, %s, %s)
        ON CONFLICT (entity_id, item_id, source_type) DO NOTHING
        """,
        (entity_id, item_id, source_type),
    )


def count_sources_for_entity(conn: Connection, entity_id: str) -> int:
    """Return the count of distinct item_ids that have contributed to this
    entity, from the wiki.page_sources ledger — the deterministic record of
    which content item surfaced which entity, not the LLM-authored
    wiki.pages.sources array.
    """
    row = conn.execute(
        "SELECT count(DISTINCT item_id) FROM wiki.page_sources WHERE entity_id = %s",
        (entity_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def is_source_for_entity(conn: Connection, entity_id: str, item_id: str) -> bool:
    """True if item_id is already a recorded contribution for entity_id.

    Lets the producer decide whether the current tick adds a new source (so
    num_sources reflects the post-commit ledger state) without double-counting
    a re-processed item.
    """
    row = conn.execute(
        "SELECT 1 FROM wiki.page_sources WHERE entity_id = %s AND item_id = %s LIMIT 1",
        (entity_id, item_id),
    ).fetchone()
    return row is not None


def snapshot_aliases(conn: Connection) -> AliasStore:
    """Read every alias row into an in-memory AliasStore for prompt use."""
    rows = conn.execute(
        "SELECT entity_id, canonical_name, alias FROM wiki.aliases ORDER BY entity_id, alias"
    ).fetchall()

    entries: dict[str, AliasEntry] = {}
    for entity_id, canonical, alias in rows:
        if entity_id not in entries:
            entries[entity_id] = AliasEntry(canonical=canonical, aliases=[])
        if alias != canonical:
            entries[entity_id].aliases.append(alias)
    return AliasStore(entries=entries)
