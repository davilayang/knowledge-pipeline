"""SQLite layer for the deferred-learning queue.

Two tables:

- `queue_items` — one row per Notion Queue page (cohort identity, fetch
  provenance, extraction-cohort summary fields).
- `extraction_calls` — one row per LLM call (output + provenance). Multiple
  rows per (notion_page_id, call_kind) are allowed for LangGraph refinement
  loops; readers take the most-recent via `extracted_at DESC`.

The orchestrator's extract_complex_contents pipeline owns the writes
(`upsert_fetched` + `record_extraction_calls`); newsletter-assistant reads
via `get_queue_extraction` (legacy single-blob shape) or directly against
`extraction_calls` (three-call shape) on the same SQLite file in mode=ro.

The legacy single-blob columns on `queue_items` (`extraction_payload`,
`extraction_prompt_label`, `prompt_sha256`, `tokens_in`, `tokens_out`) are
RETAINED in this release cycle for cheap rollback. They will be dropped in
a follow-up once three-call quality is confirmed in prod.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from domains.extraction.records import ExtractionCallRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    notion_page_id              TEXT PRIMARY KEY,
    url                         TEXT NOT NULL,
    canonical_url               TEXT,
    content_type                TEXT,

    -- fetch
    raw_content                 TEXT,
    fetched_at                  TEXT,
    fetch_tier                  TEXT,
    fetch_tier_log              TEXT,
    fetched_content_char_count  INTEGER,
    content_hash                TEXT,

    -- extraction provenance (operational; indexed)
    extracted_at                TEXT,
    extraction_prompt_label     TEXT,    -- legacy single-shot path; retained for rollback
    extraction_model            TEXT,
    prompt_sha256               TEXT,    -- legacy single-shot path; retained for rollback
    tokens_in                   INTEGER, -- legacy single-shot path; retained for rollback
    tokens_out                  INTEGER, -- legacy single-shot path; retained for rollback

    -- legacy Topic Card content (output now lives in extraction_calls; retained for rollback)
    extraction_payload          TEXT,

    -- three-call cohort summary (per-call detail lives in extraction_calls)
    extractor_label             TEXT,    -- "3call_v1" today; "graph_v3" later
    extractor_sha256            TEXT,    -- bundle hash across the three sub-prompts
    tokens_in_total             INTEGER, -- denormalised sum across extraction_calls rows
    tokens_out_total            INTEGER,
    langfuse_trace_id           TEXT,    -- nullable; deep-trace pointer (LangGraph era)

    error_text                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_items_url
    ON queue_items(url);
CREATE INDEX IF NOT EXISTS idx_queue_items_content_type
    ON queue_items(content_type);
CREATE INDEX IF NOT EXISTS idx_queue_items_prompt_label
    ON queue_items(extraction_prompt_label);
CREATE INDEX IF NOT EXISTS idx_queue_items_extracted_at
    ON queue_items(extracted_at);
CREATE INDEX IF NOT EXISTS idx_queue_items_extractor_label
    ON queue_items(extractor_label);

CREATE TABLE IF NOT EXISTS extraction_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    notion_page_id      TEXT NOT NULL REFERENCES queue_items(notion_page_id) ON DELETE CASCADE,
    call_kind           TEXT NOT NULL,        -- narrative | topic_card | followups
    prompt_label        TEXT NOT NULL,
    prompt_sha256       TEXT NOT NULL,
    schema_name         TEXT,                 -- "TopicCard" | "Followups" | NULL for narrative
    model               TEXT NOT NULL,
    output              TEXT NOT NULL,        -- markdown narrative; pydantic-JSON for structured
    tokens_in           INTEGER NOT NULL,
    tokens_out          INTEGER NOT NULL,
    cached_tokens       INTEGER,
    duration_ms         REAL,
    extracted_at        TEXT NOT NULL,
    node_metadata       TEXT                  -- nullable JSON; LangGraph node extras
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
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        # Run idempotent column additions before executescript so the indexes
        # that reference those columns (e.g. content_type) don't fail on
        # pre-existing DBs that lack the column.
        for ddl in (
            "ALTER TABLE queue_items ADD COLUMN canonical_url TEXT",
            "ALTER TABLE queue_items ADD COLUMN content_type TEXT",
            "ALTER TABLE queue_items ADD COLUMN extraction_payload TEXT",
            "ALTER TABLE queue_items ADD COLUMN extractor_label TEXT",
            "ALTER TABLE queue_items ADD COLUMN extractor_sha256 TEXT",
            "ALTER TABLE queue_items ADD COLUMN tokens_in_total INTEGER",
            "ALTER TABLE queue_items ADD COLUMN tokens_out_total INTEGER",
            "ALTER TABLE queue_items ADD COLUMN langfuse_trace_id TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                # SQLite ≥3.x guarantees these exact message strings — stable across versions.
                if "duplicate column name" not in str(exc).lower():
                    # Table doesn't exist yet — fine, executescript will create it.
                    if "no such table" not in str(exc).lower():
                        raise
        conn.executescript(_SCHEMA)


def upsert_triaged(
    *,
    db_path: Path,
    notion_page_id: str,
    url: str,
    canonical_url: str,
    content_type: str,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO queue_items (notion_page_id, url, canonical_url, content_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                url = excluded.url,
                canonical_url = excluded.canonical_url,
                content_type = excluded.content_type,
                error_text = NULL
            """,
            (notion_page_id, url, canonical_url, content_type),
        )


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
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO queue_items (
                notion_page_id, url, raw_content, fetched_at, fetch_tier,
                fetch_tier_log, fetched_content_char_count, content_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notion_page_id) DO UPDATE SET
                url = excluded.url,
                raw_content = excluded.raw_content,
                fetched_at = excluded.fetched_at,
                fetch_tier = excluded.fetch_tier,
                fetch_tier_log = excluded.fetch_tier_log,
                fetched_content_char_count = excluded.fetched_content_char_count,
                content_hash = excluded.content_hash,
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
            ),
        )


def update_extracted(
    *,
    db_path: Path,
    notion_page_id: str,
    extraction: dict[str, Any],
    prompt_label: str,
    prompt_sha256: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE queue_items SET
                extraction_payload = ?,
                extraction_prompt_label = ?,
                extraction_model = ?,
                prompt_sha256 = ?,
                tokens_in = ?,
                tokens_out = ?,
                extracted_at = ?,
                error_text = NULL
            WHERE notion_page_id = ?
            """,
            (
                json.dumps(extraction),
                prompt_label,
                model,
                prompt_sha256,
                tokens_in,
                tokens_out,
                _now_iso(),
                notion_page_id,
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
    history naturally. Readers take the most-recent via `extracted_at DESC`.

    `queue_items.extracted_at` is the max across the supplied calls — cohort
    completion timestamp."""
    extracted_at = max(c.extracted_at for c in calls)
    with _connect(db_path) as conn:
        for c in calls:
            conn.execute(
                """
                INSERT INTO extraction_calls (
                    notion_page_id, call_kind, prompt_label, prompt_sha256,
                    schema_name, model, output, tokens_in, tokens_out,
                    cached_tokens, duration_ms, extracted_at, node_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notion_page_id,
                    c.call_kind,
                    c.prompt_label,
                    c.prompt_sha256,
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
    per call_kind.

    Empty dict when the page has no extraction_calls rows (e.g. fresh row,
    legacy single-shot row pre-migration)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT call_kind, prompt_label, prompt_sha256, schema_name,
                   model, output, tokens_in, tokens_out, cached_tokens,
                   duration_ms, extracted_at, node_metadata
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
    cutoff = (datetime.now(UTC) - timedelta(minutes=min_age_minutes)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT notion_page_id, url, extracted_at, extraction_prompt_label
            FROM queue_items
            WHERE extracted_at IS NOT NULL AND extracted_at < ?
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_queue_extraction(*, db_path: Path, notion_page_id: str) -> dict[str, Any] | None:
    """Public consumer API. Same-machine read path for newsletter-assistant.

    Returns the flattened extraction payload merged with provenance fields, or
    None when the page hasn't been extracted yet. Excludes raw_content —
    consumers wanting the underlying body should re-fetch from the URL rather
    than depending on the kp store layout."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT url, canonical_url, content_type, extraction_payload,
                   extraction_prompt_label, extraction_model, extracted_at, content_hash
            FROM queue_items
            WHERE notion_page_id = ? AND extracted_at IS NOT NULL
            """,
            (notion_page_id,),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["extraction_payload"] or "{}")
    return {
        "url": row["url"],
        "canonical_url": row["canonical_url"],
        "content_type": row["content_type"],
        **payload,
        "extraction_prompt_label": row["extraction_prompt_label"],
        "extraction_model": row["extraction_model"],
        "extracted_at": row["extracted_at"],
        "content_hash": row["content_hash"],
    }
