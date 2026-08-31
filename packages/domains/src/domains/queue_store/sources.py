"""SQLite layer for the deferred-learning queue.

Two tables — full per-column descriptions live as inline ``--`` comments
inside the ``_SCHEMA`` block below:

- ``queue_items`` — one row per Notion Queue page. Carries cohort identity
  (``notion_page_id``, ``url``, ``canonical_url``, ``content_type``), fetch
  provenance (``raw_content`` + tier log + hash), and the cohort-level
  extraction summary (``extracted_at``, ``extraction_model``,
  ``extractor_label``, ``extractor_sha256``, ``tokens_in/out_total``).
- ``extraction_calls`` — one row per LLM call. Carries output + per-call
  provenance (``prompt_label`` / ``prompt_sha256`` / ``schema_name`` /
  ``model``) + usage (``tokens_in/out``, ``cached_tokens``, ``duration_ms``)
  + the ``node_metadata`` JSON slot for future LangGraph nodes.

Multiple rows per ``(notion_page_id, call_kind)`` are allowed — future
LangGraph refinement loops accumulate history; readers take the most-recent
via ``ORDER BY extracted_at DESC, id DESC``.

The orchestrator's ``fetch_extract_queue`` pipeline owns the writes
(``upsert_fetched`` + ``record_extraction_calls``); newsletter-assistant
reads ``get_queue_extraction`` (flat dict view, composed from the latest
``topic_card`` row) or directly against ``extraction_calls`` on the same
SQLite file in mode=ro.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from domains.extraction.records import ExtractionCallRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    notion_page_id              TEXT PRIMARY KEY,  -- pipeline partition key
    url                         TEXT NOT NULL,     -- captured Notion Queue URL
    canonical_url               TEXT,              -- normalize_url() output
    content_type                TEXT,              -- YouTube/arXiv/Article/Other
    content_shape               TEXT,              -- conference_talk/... NULL→"unknown"
    enrichment_json             TEXT,              -- JSON; signals cache from `enriched` asset
    raw_content                 TEXT,              -- fetched body
    raw_content_override        TEXT NOT NULL DEFAULT '',  -- user-pasted body
    user_comments_json          TEXT,              -- verbatim Notion comments; cohort-scoped
    fetched_at                  TEXT,              -- ISO-8601 UTC
    fetch_tier                  TEXT,              -- winning fetcher
    fetch_tier_log              TEXT,              -- JSON; per-tier attempts
    fetched_content_char_count  INTEGER,           -- gates "below floor"
    content_hash                TEXT,              -- SHA-256 of raw_content
    title                       TEXT,              -- fetcher metadata: article title
    author                      TEXT,              -- fetcher metadata: author/byline
    content_date                TEXT,              -- fetcher metadata: publication date (ISO)
    contributors_json           TEXT,              -- JSON [{name, role, affiliation}] read off
                                                   -- the body. `[]` = none found, NULL = never
                                                   -- extracted. Not `author`, which stays the
                                                   -- platform's raw byline.
    publisher                   TEXT,              -- who published it (channel / site / show)
    unreadable_json             TEXT,              -- JSON array of substance the body refers to
                                                   -- but does not contain; a `major` entry means
                                                   -- the fetch arrived damaged and the row failed
    extracted_at                TEXT,              -- cohort completion ts
    extraction_model            TEXT,              -- cohort model
    extractor_label             TEXT,              -- "3call_v1" etc.
    extractor_sha256            TEXT,              -- model + 3-prompt hash
    tokens_in_total             INTEGER,           -- sum across calls
    tokens_out_total            INTEGER,           -- sum across calls
    langfuse_trace_id           TEXT,              -- nullable (LangGraph era)
    error_text                  TEXT               -- cohort failure msg
);

CREATE INDEX IF NOT EXISTS idx_queue_items_url
    ON queue_items(url);
CREATE INDEX IF NOT EXISTS idx_queue_items_canonical_url
    ON queue_items(canonical_url);
CREATE INDEX IF NOT EXISTS idx_queue_items_content_type
    ON queue_items(content_type);
CREATE INDEX IF NOT EXISTS idx_queue_items_extracted_at
    ON queue_items(extracted_at);
CREATE INDEX IF NOT EXISTS idx_queue_items_extractor_label
    ON queue_items(extractor_label);

CREATE TABLE IF NOT EXISTS extraction_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,  -- latest-tiebreaker
    notion_page_id    TEXT NOT NULL                       -- FK; cohort link
                      REFERENCES queue_items(notion_page_id) ON DELETE CASCADE,
    call_kind         TEXT NOT NULL,                      -- narrative/topic_card/followups
    prompt_label      TEXT NOT NULL,                      -- e.g. "topic_card_v1"
    prompt_sha256     TEXT NOT NULL,                      -- per-call staleness
    prompt_set_shape  TEXT,                               -- bundle shape; NULL→"unknown"
    schema_name       TEXT,                               -- pydantic model name, or NULL
    model             TEXT NOT NULL,                      -- per-call model
    output            TEXT NOT NULL,                      -- markdown or pydantic-JSON
    tokens_in         INTEGER NOT NULL,
    tokens_out        INTEGER NOT NULL,
    cached_tokens     INTEGER,                            -- nullable; prefix-cache hits
    duration_ms       REAL,                               -- per-call latency
    extracted_at      TEXT NOT NULL,                      -- ISO-8601 UTC
    node_metadata     TEXT                                -- nullable JSON; LangGraph
);

CREATE INDEX IF NOT EXISTS idx_extraction_calls_page
    ON extraction_calls(notion_page_id);
CREATE INDEX IF NOT EXISTS idx_extraction_calls_call_kind
    ON extraction_calls(call_kind);
CREATE INDEX IF NOT EXISTS idx_extraction_calls_prompt_label
    ON extraction_calls(call_kind, prompt_label);
CREATE INDEX IF NOT EXISTS idx_extraction_calls_extracted_at
    ON extraction_calls(notion_page_id, extracted_at DESC);
"""

# Legacy single-shot columns dropped in this release. Listed here so the
# idempotent DROPs below find them on any DB that still carries them, and so
# the next-cycle reader sees the canonical list.
_LEGACY_COLUMNS_TO_DROP = (
    "extraction_payload",
    "extraction_prompt_label",
    "prompt_sha256",
    "tokens_in",
    "tokens_out",
)

# Indexes on legacy columns must be dropped before SQLite will let us drop the
# columns themselves (SQLite ≥3.35 refuses DROP COLUMN on indexed columns).
_LEGACY_INDEXES_TO_DROP = ("idx_queue_items_prompt_label",)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    # SQLite disables FK enforcement by default per-connection. Without this,
    # the `ON DELETE CASCADE` on extraction_calls.notion_page_id is silently
    # a no-op and orphaned call rows survive a queue_items delete.
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_schema(*, db_path: Path) -> None:
    """Idempotent schema bring-up + forward migration.

    On a fresh DB: `executescript(_SCHEMA)` creates both tables with the
    current column shape; the ALTER loops are no-ops.

    On a DB carrying older columns (PR #65 / single-shot / pre-three-call):
    the idempotent ADDs / DROPs converge it to the current shape. Legacy
    columns (`extraction_payload`, `extraction_prompt_label`, `prompt_sha256`,
    `tokens_in`, `tokens_out`) and the index on `extraction_prompt_label` are
    removed — those existed only for the v1 single-shot extractor that was
    superseded by three-call extraction."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        # 1. ADD new columns (idempotent — duplicate column = no-op).
        for ddl in (
            "ALTER TABLE queue_items ADD COLUMN canonical_url TEXT",
            "ALTER TABLE queue_items ADD COLUMN content_type TEXT",
            "ALTER TABLE queue_items ADD COLUMN content_shape TEXT",
            "ALTER TABLE queue_items ADD COLUMN enrichment_json TEXT",
            "ALTER TABLE queue_items ADD COLUMN extractor_label TEXT",
            "ALTER TABLE queue_items ADD COLUMN extractor_sha256 TEXT",
            "ALTER TABLE queue_items ADD COLUMN tokens_in_total INTEGER",
            "ALTER TABLE queue_items ADD COLUMN tokens_out_total INTEGER",
            "ALTER TABLE queue_items ADD COLUMN langfuse_trace_id TEXT",
            "ALTER TABLE queue_items ADD COLUMN user_comments_json TEXT",
            "ALTER TABLE queue_items ADD COLUMN title TEXT",
            "ALTER TABLE queue_items ADD COLUMN author TEXT",
            "ALTER TABLE queue_items ADD COLUMN content_date TEXT",
            "ALTER TABLE queue_items ADD COLUMN contributors_json TEXT",
            "ALTER TABLE queue_items ADD COLUMN publisher TEXT",
            "ALTER TABLE queue_items ADD COLUMN unreadable_json TEXT",
            "ALTER TABLE extraction_calls ADD COLUMN prompt_set_shape TEXT",
        ):
            _ddl_idempotent(conn, ddl)

        # 2. DROP legacy indexes BEFORE legacy columns (SQLite refuses
        # DROP COLUMN on indexed columns).
        for idx in _LEGACY_INDEXES_TO_DROP:
            _ddl_idempotent(conn, f"DROP INDEX IF EXISTS {idx}")

        # 3. DROP legacy columns (idempotent — absent column = no-op).
        for col in _LEGACY_COLUMNS_TO_DROP:
            _ddl_idempotent(conn, f"ALTER TABLE queue_items DROP COLUMN {col}")

        # 4. Bring up the current schema (creates tables if missing,
        # creates current indexes; both `IF NOT EXISTS`).
        conn.executescript(_SCHEMA)


def _ddl_idempotent(conn: sqlite3.Connection, ddl: str) -> None:
    """Run a DDL statement; swallow the expected idempotency errors.

    Stable SQLite error messages (≥3.x):
    - "duplicate column name" → ADD on existing column
    - "no such column" → DROP on absent column
    - "no such table" → fresh DB; the executescript that follows creates it
    """
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column name" in msg or "no such column" in msg or "no such table" in msg:
            return
        raise


def upsert_triaged(
    *,
    db_path: Path,
    notion_page_id: str,
    url: str,
    canonical_url: str,
    content_type: str,
    content_shape: str | None = None,
    raw_content_override: str = "",
    user_comments_json: str | None = None,
    content_date: str | None = None,
) -> None:
    """Re-triage is a cohort boundary: clear every downstream-produced column
    so `fetched` / `extracted` re-run on the fresh routing. Without this, the
    `fetched` cache check (`if row.get("raw_content") and row.get("url")`)
    short-circuits when a row already has a body — even when the URL changed
    or the fetcher started routing it differently (the Medium-handler PR #109
    incident: stale paywall preview survived the re-queue). Extraction cohort
    fields and FK'd extraction_calls rows go too — readers of
    `get_queue_extraction` would otherwise serve last-cohort Topic Cards while
    the row is back at Status=Fetching.

    `content_shape` is the orthogonal extractor-routing axis. `None` writes
    NULL; `get_content_shape` coalesces NULL → `"unknown"` so the extractor's
    per-shape lookup falls through to the generic fallback bundle."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO queue_items (
                notion_page_id, url, canonical_url, content_type, content_shape,
                raw_content_override, user_comments_json, content_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                url = excluded.url,
                canonical_url = excluded.canonical_url,
                content_type = excluded.content_type,
                content_shape = excluded.content_shape,
                raw_content_override = excluded.raw_content_override,
                user_comments_json = excluded.user_comments_json,
                -- content_date is the "Publish Date" — a bidirectional signal, NOT
                -- a purely fetcher-produced column: it holds the user's Notion
                -- value OR a fetcher-discovered date written back to Notion. So
                -- re-triage writes the current Notion value (the fetcher later
                -- fills it only if still blank); it is NOT cleared like the
                -- fetcher-produced columns below.
                content_date = excluded.content_date,
                raw_content = NULL,
                fetched_at = NULL,
                fetch_tier = NULL,
                fetch_tier_log = NULL,
                fetched_content_char_count = NULL,
                content_hash = NULL,
                title = NULL,
                author = NULL,
                contributors_json = NULL,
                publisher = NULL,
                unreadable_json = NULL,
                extracted_at = NULL,
                extraction_model = NULL,
                extractor_label = NULL,
                extractor_sha256 = NULL,
                tokens_in_total = NULL,
                tokens_out_total = NULL,
                langfuse_trace_id = NULL,
                error_text = NULL
            """,
            (
                notion_page_id,
                url,
                canonical_url,
                content_type,
                content_shape,
                raw_content_override,
                user_comments_json,
                content_date,
            ),
        )
        # FK CASCADE on extraction_calls.notion_page_id only fires on DELETE of
        # the parent row, not on UPDATE. Wipe explicitly so a re-triage doesn't
        # leave the previous cohort's per-call rows hanging behind a stale FK.
        conn.execute(
            "DELETE FROM extraction_calls WHERE notion_page_id = ?",
            (notion_page_id,),
        )


def upsert_enriched(
    *,
    db_path: Path,
    notion_page_id: str,
    url: str,
    enrichment_json: str,
) -> None:
    """Land the `enriched` asset's signals cache. Idempotent by page_id —
    re-materialising overwrites. `enriched` runs before `triaged` in the
    asset graph, so this often creates the row. `url` is required to satisfy
    the NOT NULL column constraint and to let any reader in the enriched-
    but-not-yet-triaged window see the captured URL instead of an empty
    placeholder. When `triaged` lands after, its ON CONFLICT overwrites
    url / canonical_url / content_type; enrichment_json is preserved across
    that re-write.

    Dedup-skipped pages retain an orphan enrichment_json row by design
    (cheap, useful on re-queue). Does NOT touch routing or fetch columns;
    `upsert_triaged` owns those."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO queue_items (notion_page_id, url, enrichment_json)
            VALUES (?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                enrichment_json = excluded.enrichment_json
            """,
            (notion_page_id, url, enrichment_json),
        )


def get_content_shape(*, db_path: Path, notion_page_id: str) -> str:
    """Single source of truth for "NULL → unknown". Consumers MUST go through
    this rather than read `content_shape` off `get_row` so the extractor's
    per-shape prompt routing never KeyErrors on a None left over from a row
    triaged before the column existed."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_shape FROM queue_items WHERE notion_page_id = ?",
            (notion_page_id,),
        ).fetchone()
    if row is None:
        return "unknown"
    return row["content_shape"] or "unknown"


def find_canonical_url_duplicate(
    *,
    db_path: Path,
    canonical_url: str,
    excluding_page_id: str,
) -> str | None:
    """Look for an existing queue_items row with the same canonical_url that
    isn't `excluding_page_id`. Returns the earliest-inserted matching
    notion_page_id, or None when no duplicate exists.

    Used by triage to flag re-captures of an already-queued URL as
    `Duplicate of <page_id>`. `excluding_page_id` is the page currently
    being triaged — it must be excluded so re-triage on the same row
    (e.g. Re-Queued from Failed) doesn't false-positive against itself.

    Returns the oldest matching page by ROWID for stable forensics: when
    the user clicks through to "Duplicate of X" they always land on the
    same earliest row regardless of which later duplicate they're looking
    at."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT notion_page_id
              FROM queue_items
             WHERE canonical_url = ? AND notion_page_id != ?
             ORDER BY ROWID
             LIMIT 1
            """,
            (canonical_url, excluding_page_id),
        ).fetchone()
    return row["notion_page_id"] if row else None


def checkpoint_wal(*, db_path: Path) -> None:
    """Run `PRAGMA wal_checkpoint(TRUNCATE)` to fold the -wal sidecar back
    into the main DB file and reset -wal to zero bytes.

    Use TRUNCATE to ensure checkpoints are merged into main db
    TRUNCATE policy: Waits briefly to fully flush + reset the WAL file to zero
    bytes — the cleanest state for readers that open with `immutable=1`
    (and therefore skip the WAL sidecars entirely).

    Safe to call when there's nothing to checkpoint — SQLite returns
    immediately.
    """
    with _connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")


def upsert_fetched(
    *,
    db_path: Path,
    notion_page_id: str,
    url: str,
    raw_content: str,
    fetch_tier: str,
    fetch_tier_log: list[dict[str, Any]],
    fetched_content_char_count: int,
    content_hash: str,
    title: str | None = None,
    author: str | None = None,
    content_date: str | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO queue_items (
                notion_page_id, url, raw_content, fetched_at, fetch_tier,
                fetch_tier_log, fetched_content_char_count, content_hash,
                title, author, content_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                url = excluded.url,
                raw_content = excluded.raw_content,
                fetched_at = excluded.fetched_at,
                fetch_tier = excluded.fetch_tier,
                fetch_tier_log = excluded.fetch_tier_log,
                fetched_content_char_count = excluded.fetched_content_char_count,
                content_hash = excluded.content_hash,
                title = excluded.title,
                author = excluded.author,
                -- Deliberate policy (single scalar, no separate provenance): the
                -- FIRST non-null date wins and sticks — a Notion "Publish Date"
                -- set at triage, else the fetcher's first discovered date. The
                -- fetcher only FILLS a blank value, it never overwrites. Tradeoff
                -- accepted: the fetcher cannot auto-correct its own earlier guess
                -- (date sources are stable, so this is rare; a manual Notion edit
                -- is the correction path). If it bites, split into distinct
                -- user/fetcher date columns and prefer user at read.
                content_date = COALESCE(queue_items.content_date, excluded.content_date),
                error_text = NULL
            """,
            (
                notion_page_id,
                url,
                raw_content,
                _now_iso(),
                fetch_tier,
                json.dumps(fetch_tier_log),
                fetched_content_char_count,
                content_hash,
                title,
                author,
                content_date,
            ),
        )


def record_extraction_calls(
    *,
    db_path: Path,
    notion_page_id: str,
    extractor_label: str,
    extractor_sha256: str,
    model: str,
    calls: list[ExtractionCallRecord],
    tokens_in_total: int,
    tokens_out_total: int,
    langfuse_trace_id: str | None = None,
) -> None:
    """Three-call write path. Inserts one row per call into `extraction_calls`
    and updates `queue_items` cohort fields, both inside a single transaction.

    INSERT (not UPSERT): the AUTOINCREMENT id allows multiple rows per
    (notion_page_id, call_kind) so LangGraph refinement loops accumulate
    history naturally. Readers take the most-recent via
    `ORDER BY extracted_at DESC, id DESC`.

    `queue_items.extracted_at` is the max across the supplied calls — cohort
    completion timestamp."""
    extracted_at = max(c.extracted_at for c in calls)
    with _connect(db_path) as conn:
        for c in calls:
            conn.execute(
                """
                INSERT INTO extraction_calls (
                    notion_page_id, call_kind, prompt_label, prompt_sha256,
                    prompt_set_shape, schema_name, model, output, tokens_in,
                    tokens_out, cached_tokens, duration_ms, extracted_at,
                    node_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notion_page_id,
                    c.call_kind,
                    c.prompt_label,
                    c.prompt_sha256,
                    c.prompt_set_shape,
                    c.schema_name,
                    model,
                    c.output,
                    c.tokens_in,
                    c.tokens_out,
                    c.cached_tokens,
                    c.duration_ms,
                    c.extracted_at,
                    json.dumps(c.node_metadata) if c.node_metadata else None,
                ),
            )
        conn.execute(
            """
            UPDATE queue_items SET
                extracted_at = ?,
                extractor_label = ?,
                extractor_sha256 = ?,
                extraction_model = ?,
                tokens_in_total = ?,
                tokens_out_total = ?,
                langfuse_trace_id = ?,
                error_text = NULL
            WHERE notion_page_id = ?
            """,
            (
                extracted_at,
                extractor_label,
                extractor_sha256,
                model,
                tokens_in_total,
                tokens_out_total,
                langfuse_trace_id,
                notion_page_id,
            ),
        )


def get_latest_extraction_calls(*, db_path: Path, notion_page_id: str) -> dict[str, dict[str, Any]]:
    """Returns `{call_kind: latest_row_dict}` — the most-recent row per
    call_kind, handling LangGraph refinement loops where multiple rows exist
    per call_kind. Tiebreak on `id DESC` when timestamps match.

    Empty dict when the page has no extraction_calls rows."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT call_kind, prompt_label, prompt_sha256, prompt_set_shape,
                   schema_name, model, output, tokens_in, tokens_out,
                   cached_tokens, duration_ms, extracted_at, node_metadata
              FROM extraction_calls
             WHERE notion_page_id = ?
             ORDER BY call_kind, extracted_at DESC, id DESC
            """,
            (notion_page_id,),
        ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["call_kind"] not in latest:
            latest[row["call_kind"]] = dict(row)
    return latest


def record_claims(
    *,
    db_path: Path,
    notion_page_id: str,
    output: str,
    prompt_label: str,
    prompt_sha256: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Persist a per-source claim summary as a `extract_claims`-kind
    `extraction_calls` row. The wiki summary is an LLM extraction over the body,
    so it is kept like every other extraction output: the rendered `output` plus
    prompt provenance, INSERT-not-UPSERT (re-runs accumulate; `get_claims`
    returns the latest), and FK-cascade-cleared with the cohort on re-triage."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO extraction_calls (
                notion_page_id, call_kind, prompt_label, prompt_sha256,
                schema_name, model, output, tokens_in, tokens_out, extracted_at
            ) VALUES (?, 'extract_claims', ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                notion_page_id,
                prompt_label,
                prompt_sha256,
                model,
                output,
                tokens_in,
                tokens_out,
                _now_iso(),
            ),
        )


def get_all_claims(*, db_path: Path) -> list[tuple[str, str]]:
    """Every page's latest `extract_claims` output as `(notion_page_id, output)`,
    ordered by `notion_page_id`. The attributed-lane consumer reads the whole
    corpus in one pass; latest-wins per page mirrors `get_claims`."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT notion_page_id, output FROM extraction_calls e
             WHERE call_kind = 'extract_claims'
               AND id = (
                   SELECT id FROM extraction_calls e2
                    WHERE e2.notion_page_id = e.notion_page_id
                      AND e2.call_kind = 'extract_claims'
                    ORDER BY extracted_at DESC, id DESC
                    LIMIT 1
               )
             ORDER BY notion_page_id
            """
        ).fetchall()
    return [(row["notion_page_id"], row["output"]) for row in rows]


def get_claims(*, db_path: Path, notion_page_id: str) -> str | None:
    """The most-recent `extract_claims` output for a page, or None if none is
    recorded. Latest-wins, mirroring `get_latest_extraction_calls`."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT output FROM extraction_calls
             WHERE notion_page_id = ? AND call_kind = 'extract_claims'
             ORDER BY extracted_at DESC, id DESC
             LIMIT 1
            """,
            (notion_page_id,),
        ).fetchone()
    return row["output"] if row else None


def record_candidates(
    *,
    db_path: Path,
    notion_page_id: str,
    output: str,
    prompt_label: str,
    prompt_sha256: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int | None = None,
) -> None:
    """Persist a per-source entity-candidate set as an `extract_entities`-kind
    `extraction_calls` row — the article-grounded candidates the attributed lane
    resolves against the live wiki. Same shape as `record_claims`: the rendered
    `output` plus prompt provenance, INSERT-not-UPSERT (re-runs accumulate;
    `get_candidates` returns the latest), FK-cascade-cleared on re-triage.

    `cached_tokens` is recorded (the entities call reuses the article prompt-cache
    primed by the claims call, so its cache-hit is the number worth tracking)."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO extraction_calls (
                notion_page_id, call_kind, prompt_label, prompt_sha256,
                schema_name, model, output, tokens_in, tokens_out,
                cached_tokens, extracted_at
            ) VALUES (?, 'extract_entities', ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                notion_page_id,
                prompt_label,
                prompt_sha256,
                model,
                output,
                tokens_in,
                tokens_out,
                cached_tokens,
                _now_iso(),
            ),
        )


def record_metadata(
    *,
    db_path: Path,
    notion_page_id: str,
    contributors_json: str,
    publisher: str | None,
    unreadable_json: str,
    prompt_label: str,
    prompt_sha256: str,
    model: str,
    output: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int | None = None,
    duration_ms: float | None = None,
    content_hash: str | None = None,
    inputs_sha: str | None = None,
) -> None:
    """Persist one metadata extraction: the three `queue_items` columns plus a
    `metadata`-kind `extraction_calls` row, in one transaction so a row can never
    carry columns without the call that produced them.

    Columns are UPDATE-not-INSERT (`fetch_content` already wrote the row); the
    call row is INSERT-not-UPSERT like every other kind, so re-runs accumulate and
    the latest wins.

    `inputs_sha` in `node_metadata` is what the caller compares to skip a
    re-extraction; `content_hash` rides along as its readable half — the sha says
    a row is stale, the hash says whether the body was why."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE queue_items
               SET contributors_json = ?, publisher = ?, unreadable_json = ?
             WHERE notion_page_id = ?
            """,
            (contributors_json, publisher, unreadable_json, notion_page_id),
        )
        conn.execute(
            """
            INSERT INTO extraction_calls (
                notion_page_id, call_kind, prompt_label, prompt_sha256,
                schema_name, model, output, tokens_in, tokens_out,
                cached_tokens, duration_ms, extracted_at, node_metadata
            ) VALUES (?, 'metadata', ?, ?, 'MetadataPayload', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notion_page_id,
                prompt_label,
                prompt_sha256,
                model,
                output,
                tokens_in,
                tokens_out,
                cached_tokens,
                duration_ms,
                _now_iso(),
                (
                    json.dumps({"content_hash": content_hash, "inputs_sha": inputs_sha})
                    if (content_hash or inputs_sha)
                    else None
                ),
            ),
        )


def get_ready_extraction_docs(
    *, db_path: Path
) -> tuple[dict[str, tuple[str, str, str]], list[str]]:
    """One-snapshot read for the wiki sweep. Returns `(ready, partial)`:

    - `ready`: `{notion_page_id: (claims_doc, candidates_doc, max_extracted_at)}`
      for every page carrying BOTH a latest `extract_claims` and a latest
      `extract_entities` doc. `max_extracted_at` is derived from THOSE SAME rows,
      so the sweep's watermark can never advance past docs it didn't consume —
      all four values come from one connection's consistent snapshot (a concurrent
      `record_claims`/`record_candidates` mid-sweep can't tear old-doc/new-watermark).
    - `partial`: page_ids carrying exactly one of the two kinds — observability
      only (can't be synthesised until the other doc lands).

    Latest-per-(page, kind) via `extracted_at DESC, id DESC`, mirroring
    `get_claims`/`get_candidates`. The topic_card 3-call extraction shares the
    table but is excluded (unrelated to wiki freshness)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT notion_page_id, call_kind, output, extracted_at
              FROM extraction_calls e
             WHERE call_kind IN ('extract_claims', 'extract_entities')
               AND id = (
                   SELECT id FROM extraction_calls e2
                    WHERE e2.notion_page_id = e.notion_page_id
                      AND e2.call_kind = e.call_kind
                    ORDER BY extracted_at DESC, id DESC
                    LIMIT 1
               )
            """
        ).fetchall()
    by_page: dict[str, dict[str, tuple[str, str]]] = {}
    for row in rows:
        by_page.setdefault(row["notion_page_id"], {})[row["call_kind"]] = (
            row["output"],
            row["extracted_at"],
        )
    ready: dict[str, tuple[str, str, str]] = {}
    partial: list[str] = []
    for page_id in sorted(by_page):
        kinds = by_page[page_id]
        claims = kinds.get("extract_claims")
        candidates = kinds.get("extract_entities")
        if claims and candidates:
            ready[page_id] = (
                claims[0],
                candidates[0],
                max(claims[1], candidates[1]),
            )
        else:
            partial.append(page_id)
    return ready, partial


def get_candidates(*, db_path: Path, notion_page_id: str) -> str | None:
    """The most-recent `extract_entities` output for a page, or None if none is
    recorded. Latest-wins, mirroring `get_claims`."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT output FROM extraction_calls
             WHERE notion_page_id = ? AND call_kind = 'extract_entities'
             ORDER BY extracted_at DESC, id DESC
             LIMIT 1
            """,
            (notion_page_id,),
        ).fetchone()
    return row["output"] if row else None


def get_all_candidates(*, db_path: Path) -> list[tuple[str, str]]:
    """Every page's latest `extract_entities` output as `(notion_page_id, output)`,
    ordered by `notion_page_id` — the corpus-wide read for the attributed-lane
    consumer. Latest-wins per page, mirroring `get_all_claims`."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT notion_page_id, output FROM extraction_calls e
             WHERE call_kind = 'extract_entities'
               AND id = (
                   SELECT id FROM extraction_calls e2
                    WHERE e2.notion_page_id = e.notion_page_id
                      AND e2.call_kind = 'extract_entities'
                    ORDER BY extracted_at DESC, id DESC
                    LIMIT 1
               )
             ORDER BY notion_page_id
            """
        ).fetchall()
    return [(row["notion_page_id"], row["output"]) for row in rows]


def mark_failed(
    *, db_path: Path, notion_page_id: str, error_text: str, url: str | None = None
) -> None:
    """Record a failure for a page_id. Inserts a stub row when none exists yet
    (e.g. fetched_content failed on the first attempt) so the page_id has a
    row to flag — keeps `get_row` consistent for the failure-handling path."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO queue_items (notion_page_id, url, error_text)
            VALUES (?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                error_text = excluded.error_text
            """,
            (notion_page_id, url or "", error_text),
        )


def get_row(*, db_path: Path, notion_page_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM queue_items WHERE notion_page_id = ?",
            (notion_page_id,),
        ).fetchone()
    return dict(row) if row else None


def list_with_stale_extraction(*, db_path: Path, min_age_minutes: int) -> list[dict[str, Any]]:
    """Returns rows whose `extracted_at` is older than the cutoff.

    Used by future re-extract sensors. `extractor_label` is the cohort
    identity — comparing the row's label to the current extractor's label
    is how the sensor decides whether to re-fire vs leave alone."""
    cutoff = (datetime.now(UTC) - timedelta(minutes=min_age_minutes)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT notion_page_id, url, extracted_at, extractor_label, extractor_sha256
            FROM queue_items
            WHERE extracted_at IS NOT NULL AND extracted_at < ?
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_queue_extraction(*, db_path: Path, notion_page_id: str) -> dict[str, Any] | None:
    """Public consumer API. Same-machine read path for newsletter-assistant.

    Returns the flattened extraction payload merged with provenance fields, or
    None when the page hasn't been extracted yet. Composes the flat view from
    the latest `topic_card` row in `extraction_calls`; field shape matches
    what NA's reader has historically consumed (extracted_title /
    core_mechanism / etc. + provenance keys at top level).

    `contributors` / `publisher` come from the metadata asset and were added
    later — additive keys, so an older reader is unaffected."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT url, canonical_url, content_type, extraction_model,
                   extracted_at, content_hash,
                   contributors_json, publisher
            FROM queue_items
            WHERE notion_page_id = ? AND extracted_at IS NOT NULL
            """,
            (notion_page_id,),
        ).fetchone()
    if row is None:
        return None
    latest = get_latest_extraction_calls(db_path=db_path, notion_page_id=notion_page_id)
    topic_row = latest.get("topic_card")
    topic_payload = json.loads(topic_row["output"]) if topic_row else {}
    return {
        "url": row["url"],
        "canonical_url": row["canonical_url"],
        "content_type": row["content_type"],
        **topic_payload,
        "extraction_prompt_label": topic_row["prompt_label"] if topic_row else None,
        "extraction_model": row["extraction_model"],
        "extracted_at": row["extracted_at"],
        "content_hash": row["content_hash"],
        # Written by a different asset than the topic card, so these can be
        # empty on a row that is otherwise fully extracted. The keys are always
        # present, holding empty values, so the consumer never branches on a
        # missing key. Note the view is gated on `extracted_at`, which the
        # metadata asset does not set: metadata for an item whose reading-card
        # extraction failed is stored but not visible here.
        "contributors": json.loads(row["contributors_json"]) if row["contributors_json"] else [],
        "publisher": row["publisher"],
    }
