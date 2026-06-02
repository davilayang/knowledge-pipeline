"""SQLite layer for the deferred-learning queue.

One table: `queue_items`. The orchestrator's extract_queued_items pipeline
owns the writes (upsert_fetched + update_extracted); newsletter-assistant
reads via get_queue_extraction against the same SQLite file in mode=ro.

UPDATE-on-re-extract is intentional policy. Bumping the extraction prompt
label overwrites the prior extraction. The Topic Card shape is owned by the
prompt and stored as JSON in `extraction_payload` — per-content-type
heterogeneity (YouTube/arXiv/Article) needs zero schema migration as prompts
iterate.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
    extraction_prompt_label     TEXT,
    extraction_model            TEXT,
    prompt_sha256               TEXT,
    tokens_in                   INTEGER,
    tokens_out                  INTEGER,

    -- Topic Card content (shape owned by the prompt; per-content-type heterogeneous)
    extraction_payload          TEXT,

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
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
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
