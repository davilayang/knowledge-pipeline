"""SQLite helpers for wiki state — pure functions taking a sqlite3 connection.

Single source of truth for the wiki.db identity/page tables: entities /
processed_items / pages / aliases / entity_relations / rejected_entities (the
claim-centric sources / claims / claim_entities live in attributed.py). Callers
manage connection lifecycle and transactions (`with conn:` for an atomic block);
these helpers issue SQL only.

Identity lives in `entities` (the opaque surrogate `entity_id`); `pages` is the
synthesised-artifact record (1:1, FK to entities) and no longer carries
page_type/slug/canonical_name — those are read back by joining `entities`.
`build_entity_index` reads the whole identity snapshot into the in-memory
`EntityIndex` the resolver runs against.

`connect()` opens a WAL connection with the standard pragmas (mirrors the
sibling stores raw_store.db / queue.db); `create_schema()` applies wiki.sql.
The schema lives in packages/domains/src/domains/wiki/schema/wiki.sql.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from domains.wiki.aliases import AliasEntry, AliasStore
from domains.wiki.identity import EntityIndex, EntityRecord, normalize_name

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "wiki.sql"


def _now_iso() -> str:
    """UTC timestamp as ISO-8601 (seconds precision), matching the sibling
    SQLite stores so timestamps compare/parse uniformly across wiki.db /
    queue.db / raw_store.db."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a wiki.db connection with WAL + busy_timeout (sibling-store defaults).

    Default isolation_level is kept (not autocommit) so `with conn:` brackets an
    atomic transaction — the commit path writes entities + pages + aliases +
    processed_items all-or-nothing. row_factory is sqlite3.Row
    (positional unpacking still works, plus name access for ad-hoc reads).
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
    """A page joined with its identity — what consumers (toc, indexing) read.

    The `pages` table stores only entity_id/file_path/related_ids/updated_at;
    canonical_name/slug/page_type come from the `entities` join so they have a
    single authoritative home.
    """

    entity_id: str
    canonical_name: str
    slug: str
    page_type: str
    file_path: str
    related_ids: list[str]
    updated_at: str


def _json_list(value: str | None) -> list[str]:
    """Decode a JSON-array TEXT column to a list; tolerate NULL/empty."""
    if not value:
        return []
    return json.loads(value)


# --------------------------------------------------------------------------
# entities — the identity record
# --------------------------------------------------------------------------


def insert_entity(conn: sqlite3.Connection, entity: EntityRecord) -> None:
    """Insert one identity row. Caller manages the transaction and writes
    entities BEFORE the pages/aliases/entity_relations that FK to them.

    ON CONFLICT(entity_id) DO NOTHING keeps a replay of the same surrogate
    idempotent; a genuine normalized_name collision still raises (fail fast —
    the resolver's exact-match gate means it shouldn't happen in sequential
    processing).
    """
    conn.execute(
        """
        INSERT INTO entities
            (entity_id, canonical_name, normalized_name, slug, page_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (entity_id) DO NOTHING
        """,
        (
            entity.entity_id,
            entity.canonical_name,
            entity.normalized_name,
            entity.slug,
            entity.page_type,
            entity.created_at,
        ),
    )


def get_entity(conn: sqlite3.Connection, entity_id: str) -> EntityRecord | None:
    """Return one identity row by entity_id, or None if absent."""
    row = conn.execute(
        """
        SELECT entity_id, canonical_name, normalized_name, slug, page_type, created_at
        FROM entities
        WHERE entity_id = ?
        """,
        (entity_id,),
    ).fetchone()
    return EntityRecord(*row) if row else None


def get_all_entities(conn: sqlite3.Connection) -> list[EntityRecord]:
    """Return every identity row, ordered by entity_id."""
    rows = conn.execute(
        """
        SELECT entity_id, canonical_name, normalized_name, slug, page_type, created_at
        FROM entities
        ORDER BY entity_id
        """
    ).fetchall()
    return [EntityRecord(*r) for r in rows]


def get_all_aliases(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return every (alias_display, entity_id) pair, ordered for determinism."""
    rows = conn.execute("SELECT alias, entity_id FROM aliases ORDER BY entity_id, alias").fetchall()
    return [(r[0], r[1]) for r in rows]


def build_entity_index(conn: sqlite3.Connection) -> EntityIndex:
    """Read the whole identity snapshot into the resolver's in-memory index."""
    return EntityIndex.build(get_all_entities(conn), get_all_aliases(conn))


# --------------------------------------------------------------------------
# processed_items
# --------------------------------------------------------------------------


def insert_processed(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source_type: str,
    status: str,
    error: str | None = None,
) -> None:
    """Upsert a processed_items row. Caller manages transaction."""
    conn.execute(
        """
        INSERT INTO processed_items (item_id, source_type, status, error, processed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (item_id, source_type)
        DO UPDATE SET status = excluded.status,
                      error = excluded.error,
                      processed_at = excluded.processed_at
        """,
        (item_id, source_type, status, error, _now_iso()),
    )


def get_processed_ids(conn: sqlite3.Connection, status: str = "ok") -> set[str]:
    """Return the set of item_ids with the given status."""
    rows = conn.execute(
        "SELECT item_id FROM processed_items WHERE status = ?",
        (status,),
    ).fetchall()
    return {r[0] for r in rows}


def get_failed(conn: sqlite3.Connection) -> list[ProcessedRecord]:
    """Return all processed_items rows with status='error', most recent first."""
    rows = conn.execute(
        """
        SELECT item_id, source_type, status, error, processed_at
        FROM processed_items
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
    entity_id: str,
    file_path: str,
    related_ids: list[str],
) -> None:
    """Upsert a pages row. Caller manages transaction and has already inserted
    the entity (pages.entity_id FKs to entities)."""
    conn.execute(
        """
        INSERT INTO pages (entity_id, file_path, related_ids, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (entity_id)
        DO UPDATE SET file_path = excluded.file_path,
                      related_ids = excluded.related_ids,
                      updated_at = excluded.updated_at
        """,
        (entity_id, file_path, json.dumps(related_ids), _now_iso()),
    )


_PAGE_SELECT = """
    SELECT p.entity_id, e.canonical_name, e.slug, e.page_type,
           p.file_path, p.related_ids, p.updated_at
    FROM pages p
    JOIN entities e ON e.entity_id = p.entity_id
"""


def get_page(conn: sqlite3.Connection, entity_id: str) -> PageRecord | None:
    """Return one page (joined with its identity) by entity_id, or None."""
    row = conn.execute(
        _PAGE_SELECT + "WHERE p.entity_id = ?",
        (entity_id,),
    ).fetchone()
    return _row_to_page(row) if row else None


def get_all_pages(conn: sqlite3.Connection) -> list[PageRecord]:
    """Return every page (joined with its identity), ordered by entity_id."""
    rows = conn.execute(_PAGE_SELECT + "ORDER BY p.entity_id").fetchall()
    return [_row_to_page(r) for r in rows]


def _row_to_page(row: sqlite3.Row) -> PageRecord:
    entity_id, canonical_name, slug, page_type, file_path, related_ids, updated_at = row
    return PageRecord(
        entity_id=entity_id,
        canonical_name=canonical_name,
        slug=slug,
        page_type=page_type,
        file_path=file_path,
        related_ids=_json_list(related_ids),
        updated_at=updated_at,
    )


# --------------------------------------------------------------------------
# aliases
# --------------------------------------------------------------------------


def insert_aliases(
    conn: sqlite3.Connection,
    pairs: list[tuple[str, str]],
) -> None:
    """Insert (alias_display, entity_id) rows, keyed on normalized_alias.

    The normalized_alias (lower/trim/collapse-ws) is the globally-unique match
    key; ON CONFLICT DO NOTHING means the first writer keeps the alias if two
    entities both claim it. Dedupes within the batch on the normalized key.

    A normalized alias already in `rejected_entities` is dropped: an alias must
    never contradict the denylist (otherwise a rejected surface form could
    re-enter as an alias of a different entity and re-resolve to it).
    """
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for display, entity_id in pairs:
        if not display:
            continue
        norm = normalize_name(display)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        rows.append((display, norm, entity_id))

    if not rows:
        return

    placeholders = ",".join("?" * len(rows))
    rejected = {
        r[0]
        for r in conn.execute(
            f"SELECT normalized_name FROM rejected_entities "
            f"WHERE normalized_name IN ({placeholders})",
            [norm for _, norm, _ in rows],
        ).fetchall()
    }
    rows = [r for r in rows if r[1] not in rejected]
    if not rows:
        return

    conn.executemany(
        """
        INSERT INTO aliases (alias, normalized_alias, entity_id)
        VALUES (?, ?, ?)
        ON CONFLICT (normalized_alias) DO NOTHING
        """,
        rows,
    )


def get_aliases_for_entity(conn: sqlite3.Connection, entity_id: str) -> list[str]:
    """Return every alias display form for `entity_id`, sorted ascending."""
    rows = conn.execute(
        "SELECT alias FROM aliases WHERE entity_id = ? ORDER BY alias",
        (entity_id,),
    ).fetchall()
    return [r[0] for r in rows]


def snapshot_aliases(conn: sqlite3.Connection) -> AliasStore:
    """Read identity + aliases into an in-memory AliasStore for prompt use.

    canonical_name comes from `entities` (its authoritative home); aliases are
    LEFT-joined so an entity with no extra alias rows still appears.
    """
    rows = conn.execute(
        """
        SELECT e.entity_id, e.canonical_name, a.alias
        FROM entities e
        LEFT JOIN aliases a ON a.entity_id = e.entity_id
        ORDER BY e.entity_id, a.alias
        """
    ).fetchall()

    entries: dict[str, AliasEntry] = {}
    for entity_id, canonical, alias in rows:
        if entity_id not in entries:
            entries[entity_id] = AliasEntry(canonical=canonical, aliases=[])
        if alias and alias != canonical:
            entries[entity_id].aliases.append(alias)
    return AliasStore(entries=entries)


# --------------------------------------------------------------------------
# entity_relations — accumulated co-occurrence edges (#54)
# --------------------------------------------------------------------------


def insert_entity_relation(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    related_entity_id: str,
    item_id: str,
    source_type: str,
    added_at: str | None = None,
) -> None:
    """Record one directed co-occurrence edge (entity_id → related_entity_id)
    contributed by content item (item_id, source_type) in the entity_relations
    ledger (#54). Idempotent (ON CONFLICT DO NOTHING) so retries and
    re-processing don't duplicate the edge — the derived co_count stays stable.
    Caller manages the transaction and has already inserted both entities (FK)."""
    conn.execute(
        """
        INSERT INTO entity_relations (entity_id, related_entity_id, item_id, source_type, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (entity_id, related_entity_id, item_id, source_type) DO NOTHING
        """,
        (entity_id, related_entity_id, item_id, source_type, added_at or _now_iso()),
    )


def get_related_for_entity(
    conn: sqlite3.Connection, entity_id: str, *, limit: int = 20
) -> list[str]:
    """Accumulated related entity_ids for a page (#54), strongest first.

    Derives `co_count = COUNT(DISTINCT item_id)` (how many distinct articles
    co-mention the pair) over the entity_relations ledger, ranked
    `co_count DESC, last_seen DESC, related_entity_id ASC` — a stable, bounded
    (top-`limit`) list for the page's `related` frontmatter. Empty for an entity
    with no recorded edges."""
    rows = conn.execute(
        """
        SELECT related_entity_id
        FROM entity_relations
        WHERE entity_id = ?
        GROUP BY related_entity_id
        ORDER BY COUNT(DISTINCT item_id) DESC, MAX(added_at) DESC, related_entity_id ASC
        LIMIT ?
        """,
        (entity_id, limit),
    ).fetchall()
    return [r["related_entity_id"] for r in rows]


# --------------------------------------------------------------------------
# rejected_entities — curator denylist (#15)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectedRecord:
    """One curator rejection — an authored "this name is not a page" decision.
    Name-keyed so it survives a from-empty rebuild (see wiki.sql)."""

    normalized_name: str
    category: str | None
    reason: str | None
    rejected_at: str


def upsert_rejected(
    conn: sqlite3.Connection,
    *,
    normalized_name: str,
    category: str | None = None,
    reason: str | None = None,
    rejected_at: str | None = None,
) -> None:
    """Upsert a rejected_entities row (name-keyed). Caller manages transaction."""
    conn.execute(
        """
        INSERT INTO rejected_entities (normalized_name, category, reason, rejected_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (normalized_name)
        DO UPDATE SET category = excluded.category,
                      reason = excluded.reason,
                      rejected_at = excluded.rejected_at
        """,
        (normalized_name, category, reason, rejected_at or _now_iso()),
    )


def get_rejected(conn: sqlite3.Connection) -> list[RejectedRecord]:
    """Return all curator rejections, ordered by normalized_name (deterministic)."""
    rows = conn.execute(
        """
        SELECT normalized_name, category, reason, rejected_at
        FROM rejected_entities
        ORDER BY normalized_name
        """
    ).fetchall()
    return [RejectedRecord(*r) for r in rows]


@dataclass(frozen=True)
class RejectResult:
    """What the caller needs after a rejection to finish the file-system side:
    unlink the entity's `.md` (file_path, may be None) and log which names were
    tombstoned (rejected_names = the canonical name plus every alias)."""

    entity_id: str
    file_path: str | None
    rejected_names: list[str]


def reject_entity(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    category: str | None = None,
    reason: str | None = None,
    rejected_at: str | None = None,
) -> RejectResult:
    """Reject (delete) an entity in one transaction (caller brackets `with
    conn:`). Tombstones the canonical name AND every alias into
    `rejected_entities` (alias-family tombstone — stops a deleted entity
    re-minting under a known surface form), then deletes the entity (children
    cascade). Returns the file_path so the caller can unlink the `.md`. Pure DB.

    Policy: the denylist is name-keyed, so it blocks only KNOWN surface forms
    (the canonical name + the aliases seen at reject time). A future article that
    mentions a brand-new synonym never recorded as an alias will re-mint the
    entity — the curator rejects it again (or merges). This is intentional;
    name-keyed suppression can't anticipate an unseen synonym.

    Single-writer only: the entity/alias reads happen before the first write, so
    they are not a locked snapshot under concurrent writers. Safe under the
    repo's single-writer SQLite model (in-cluster, off the synthesis window);
    don't run it concurrently with synthesis.
    """
    ent = get_entity(conn, entity_id)
    if ent is None:
        raise ValueError(f"entity {entity_id} does not exist")

    page_row = conn.execute(
        "SELECT file_path FROM pages WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    file_path = page_row[0] if page_row else None

    # Alias-family tombstone: the canonical name + every normalized alias.
    alias_rows = conn.execute(
        "SELECT normalized_alias FROM aliases WHERE entity_id = ?", (entity_id,)
    ).fetchall()
    rejected_names = sorted({ent.normalized_name} | {r[0] for r in alias_rows})
    for name in rejected_names:
        upsert_rejected(
            conn,
            normalized_name=name,
            category=category,
            reason=reason,
            rejected_at=rejected_at,
        )

    # Delete the entity; pages/aliases/entity_relations/claim_entities rows
    # cascade away (ON DELETE CASCADE).
    conn.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))

    return RejectResult(entity_id=entity_id, file_path=file_path, rejected_names=rejected_names)
