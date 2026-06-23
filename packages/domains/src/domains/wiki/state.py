"""SQLite helpers for wiki state — pure functions taking a sqlite3 connection.

Single source of truth for the wiki.db tables: entities / processed_items /
pages / aliases / page_sources. Callers manage connection lifecycle and
transactions (`with conn:` for an atomic block); these helpers issue SQL only.

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
    page_sources + processed_items all-or-nothing. row_factory is sqlite3.Row
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


@dataclass(frozen=True)
class PageVersionMeta:
    """One row of a page's edition history (#47) WITHOUT the full body — the
    index a consumer scans to pick a version. Fetch the body with
    get_page_version(entity_id, version)."""

    entity_id: str
    version: int
    created_at: str
    content_hash: str
    summary: str
    num_sources: int
    source_id: str
    source_type: str


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
    entities BEFORE the pages/aliases/page_sources that FK to them.

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
    content_hash: str | None = None,
    current_version: int | None = None,
) -> None:
    """Upsert a pages row. Caller manages transaction and has already inserted
    the entity (pages.entity_id FKs to entities).

    content_hash/current_version are the HEAD pointers into page_versions (#47) —
    the semantic hash of the current edition and its version number. The version
    gate (see get_page_head) supplies them; callers that don't version pass None.
    """
    conn.execute(
        """
        INSERT INTO pages (entity_id, file_path, related_ids, updated_at,
                           content_hash, current_version)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (entity_id)
        DO UPDATE SET file_path = excluded.file_path,
                      related_ids = excluded.related_ids,
                      updated_at = excluded.updated_at,
                      content_hash = excluded.content_hash,
                      current_version = excluded.current_version
        """,
        (entity_id, file_path, json.dumps(related_ids), _now_iso(), content_hash, current_version),
    )


def get_page_head(conn: sqlite3.Connection, entity_id: str) -> tuple[str | None, int]:
    """HEAD pointer for a page's version history (#47): (content_hash, version).

    Returns (None, 0) when the page has no row or no recorded edition yet — so a
    brand-new page's first synthesis reads version 0 and appends v1. O(1): reads
    the forward pointers off `pages`, never scans page_versions."""
    row = conn.execute(
        "SELECT content_hash, current_version FROM pages WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if row is None or row["current_version"] is None:
        return (None, 0)
    return (row["content_hash"], int(row["current_version"]))


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
# page_sources
# --------------------------------------------------------------------------


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
        INSERT INTO page_sources (entity_id, item_id, source_type, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (entity_id, item_id, source_type) DO NOTHING
        """,
        (entity_id, item_id, source_type, _now_iso()),
    )


def insert_page_version(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    version: int,
    content_hash: str,
    summary: str,
    num_sources: int,
    source_id: str,
    source_type: str,
    content: str,
    created_at: str | None = None,
) -> None:
    """Append one immutable edition to page_versions (#47). `version` is the
    monotonic per-entity edition number (caller derives it from HEAD); `content`
    is the full markdown body at this edition. source_id/source_type tie the
    edition to the content item that triggered it — both NOT NULL, since an
    edition without provenance can't answer "what changed it". Caller manages the
    transaction and has already inserted the entity (FK)."""
    conn.execute(
        """
        INSERT INTO page_versions (
            entity_id, version, created_at, content_hash, summary,
            num_sources, source_id, source_type, content
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            version,
            created_at or _now_iso(),
            content_hash,
            summary,
            num_sources,
            source_id,
            source_type,
            content,
        ),
    )


def get_page_history(conn: sqlite3.Connection, entity_id: str) -> list[PageVersionMeta]:
    """Edition history for a page (#47), newest-first — metadata only, no bodies.

    The index the live agent scans to answer "what changed and when". Fetch a
    specific body with get_page_version(entity_id, version). Empty list for an
    entity with no recorded editions."""
    rows = conn.execute(
        """
        SELECT entity_id, version, created_at, content_hash, summary,
               num_sources, source_id, source_type
        FROM page_versions
        WHERE entity_id = ?
        ORDER BY version DESC
        """,
        (entity_id,),
    ).fetchall()
    return [
        PageVersionMeta(
            entity_id=r["entity_id"],
            version=int(r["version"]),
            created_at=r["created_at"],
            content_hash=r["content_hash"],
            summary=r["summary"],
            num_sources=int(r["num_sources"]),
            source_id=r["source_id"],
            source_type=r["source_type"],
        )
        for r in rows
    ]


def get_page_version(conn: sqlite3.Connection, entity_id: str, version: int) -> str | None:
    """Full markdown body of one past edition (#47), or None if it doesn't exist.
    Answers "what did this page say at version N"."""
    row = conn.execute(
        "SELECT content FROM page_versions WHERE entity_id = ? AND version = ?",
        (entity_id, version),
    ).fetchone()
    return row["content"] if row is not None else None


def count_sources_for_entity(conn: sqlite3.Connection, entity_id: str) -> int:
    """Return the count of distinct item_ids that have contributed to this
    entity, from the page_sources ledger — the deterministic record of which
    content item surfaced which entity, not the LLM-authored frontmatter.
    """
    row = conn.execute(
        "SELECT count(DISTINCT item_id) FROM page_sources WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


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


def get_source_ids_for_entity(conn: sqlite3.Connection, entity_id: str) -> list[str]:
    """The accumulated, distinct source item_ids for an entity from the
    page_sources ledger — the deterministic list the page's `sources` frontmatter
    should render (vs the per-item `[source_id]` the LLM emits). Ordered by first
    contribution (MIN added_at) then item_id; GROUP BY item_id collapses an item
    that appears under multiple source_types into one entry."""
    rows = conn.execute(
        """
        SELECT item_id FROM page_sources
        WHERE entity_id = ?
        GROUP BY item_id
        ORDER BY MIN(added_at), item_id
        """,
        (entity_id,),
    ).fetchall()
    return [r["item_id"] for r in rows]


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


# --------------------------------------------------------------------------
# merge_entities — the destructive dedup primitive (#15)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    """What the caller needs after a merge to finish the file-system side:
    delete drop's `.md` (drop_file_path, may be None if it had no page) and
    re-render keep's frontmatter from the now-unioned ledgers (keep_id)."""

    keep_id: str
    drop_id: str
    drop_file_path: str | None


def merge_entities(
    conn: sqlite3.Connection,
    *,
    keep_id: str,
    drop_id: str,
    alias: bool = True,
) -> MergeResult:
    """Fold `drop_id` into `keep_id` in one transaction (caller brackets `with
    conn:`). Re-points every ledger drop→keep, then deletes drop's identity row
    (children cascade). Returns drop's file_path so the caller can unlink the
    `.md` and re-render keep. Pure DB — no file I/O.
    """
    if keep_id == drop_id:
        raise ValueError(f"cannot merge entity {keep_id} into itself")

    drop = get_entity(conn, drop_id)
    if drop is None:
        raise ValueError(f"drop entity {drop_id} does not exist")
    if get_entity(conn, keep_id) is None:
        raise ValueError(f"keep entity {keep_id} does not exist")

    drop_row = conn.execute(
        "SELECT file_path FROM pages WHERE entity_id = ?", (drop_id,)
    ).fetchone()
    drop_file_path = drop_row[0] if drop_row else None

    # Pre-flight (before any write): decide whether to alias drop's name onto
    # keep. If a THIRD entity already owns that normalized name, fail loudly —
    # a silent skip would re-mint the dup next tick (codex CONCERN 1). If keep or
    # drop already owns it, the insert is redundant (skipped below).
    insert_alias_name = False
    if alias:
        owner_row = conn.execute(
            "SELECT entity_id FROM aliases WHERE normalized_alias = ?",
            (drop.normalized_name,),
        ).fetchone()
        owner = owner_row[0] if owner_row else None
        if owner is not None and owner not in (keep_id, drop_id):
            raise ValueError(
                f"cannot alias {drop.normalized_name!r} onto {keep_id}: already "
                f"owned by a different entity {owner} (likely a homonym) — re-run "
                f"with alias=False to keep the senses separate"
            )
        insert_alias_name = owner is None

    # page_sources: re-point; OR IGNORE drops the row that would collide with an
    # existing keep contribution (same item) — it's then cascade-deleted below.
    conn.execute(
        "UPDATE OR IGNORE page_sources SET entity_id = ? WHERE entity_id = ?",
        (keep_id, drop_id),
    )

    # entity_relations: re-point BOTH endpoint columns drop→keep (OR IGNORE
    # collapses an edge that now duplicates an existing keep edge), then drop the
    # self-edges that a drop↔keep co-occurrence has become.
    conn.execute(
        "UPDATE OR IGNORE entity_relations SET entity_id = ? WHERE entity_id = ?",
        (keep_id, drop_id),
    )
    conn.execute(
        "UPDATE OR IGNORE entity_relations SET related_entity_id = ? WHERE related_entity_id = ?",
        (keep_id, drop_id),
    )
    # Only keep↔keep self-edges can arise here (a drop↔keep co-occurrence after
    # the re-point); scope the delete to keep so unrelated rows are never touched.
    conn.execute(
        "DELETE FROM entity_relations WHERE entity_id = ? AND related_entity_id = ?",
        (keep_id, keep_id),
    )

    # aliases: re-point drop's existing aliases onto keep. normalized_alias is a
    # globally-unique PK and we don't touch it, so this UPDATE can never collide
    # (no OR IGNORE needed). With alias=False (the homonym escape hatch) we must
    # NOT carry drop's own NAME across — re-pointing a pre-existing self-alias
    # would silently route drop's name to keep anyway. Excluding it leaves the
    # row on drop, so it cascade-deletes and the name mints fresh next time.
    if alias:
        conn.execute(
            "UPDATE aliases SET entity_id = ? WHERE entity_id = ?",
            (keep_id, drop_id),
        )
    else:
        conn.execute(
            "UPDATE aliases SET entity_id = ? WHERE entity_id = ? AND normalized_alias != ?",
            (keep_id, drop_id, drop.normalized_name),
        )

    # aliases: write drop's own name as an alias of keep — the load-bearing line
    # that folds the next "Max plan" mention into keep instead of re-minting the
    # dup. Direct INSERT (NOT ON CONFLICT DO NOTHING): the third-entity conflict
    # already failed in pre-flight; here `insert_alias_name` is only True when the
    # name is unclaimed. `alias=False` is the homonym escape hatch — skip it so a
    # future different-sense mention mints fresh (safe false-split).
    if insert_alias_name:
        conn.execute(
            "INSERT INTO aliases (alias, normalized_alias, entity_id) VALUES (?, ?, ?)",
            (drop.canonical_name, drop.normalized_name, keep_id),
        )

    # Delete drop's identity; pages/page_versions/aliases/page_sources/
    # entity_relations rows still on drop cascade away (ON DELETE CASCADE).
    conn.execute("DELETE FROM entities WHERE entity_id = ?", (drop_id,))

    return MergeResult(keep_id=keep_id, drop_id=drop_id, drop_file_path=drop_file_path)
