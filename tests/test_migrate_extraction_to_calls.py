"""Tests for the prod migration script.

Verifies the script converges a DB carrying the legacy single-shot shape
(extraction_payload + per-call columns + the prompt_label index) to the
current three-call shape (cohort columns + extraction_calls table; legacy
columns + index dropped). Idempotent re-runs are safe."""

import sqlite3
from pathlib import Path

from domains.queue_store.sources import create_schema

import scripts.migrate_extraction_to_calls_table as migrate

# Schema mid-rollout: legacy single-shot columns + the prompt_label index
# present. Mirrors the prod queue.db state at the time the migration runs.
_LEGACY_SINGLE_SHOT_SCHEMA = """
CREATE TABLE queue_items (
    notion_page_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    canonical_url TEXT,
    content_type TEXT,
    raw_content TEXT,
    fetched_at TEXT,
    fetch_tier TEXT,
    fetch_tier_log TEXT,
    fetched_content_char_count INTEGER,
    content_hash TEXT,
    extracted_at TEXT,
    extraction_prompt_label TEXT,
    extraction_model TEXT,
    prompt_sha256 TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    extraction_payload TEXT,
    error_text TEXT
);
CREATE INDEX idx_queue_items_prompt_label
    ON queue_items(extraction_prompt_label);
"""


def _seed_legacy_db(db_path: Path) -> None:
    """Build a DB at the legacy single-shot shape with one extracted row,
    matching the prod queue.db state pre-migration."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_LEGACY_SINGLE_SHOT_SCHEMA)
        conn.execute(
            """
            INSERT INTO queue_items (
                notion_page_id, url, canonical_url, content_type,
                raw_content, content_hash,
                extracted_at, extraction_prompt_label, extraction_model,
                prompt_sha256, tokens_in, tokens_out, extraction_payload
            ) VALUES (
                'p-legacy', 'https://example.com/legacy', 'https://example.com/legacy',
                'Article', 'body', 'h',
                '2026-06-01T00:00:00+00:00', 'v5_article', 'gpt-4o-mini',
                'a' || replace(hex(zeroblob(31)), '00', '00'), 1000, 500,
                '{"extracted_title": "Legacy Title", "core_mechanism": "old mech"}'
            )
            """
        )


def test_main_returns_0_on_existing_db(tmp_path: Path):
    db = tmp_path / "queue.db"
    create_schema(db_path=db)
    assert migrate.main(db) == 0


def test_main_returns_1_when_db_missing(tmp_path: Path):
    db = tmp_path / "missing.db"
    assert migrate.main(db) == 1


def test_drops_legacy_columns_from_existing_db(tmp_path: Path):
    db = tmp_path / "queue.db"
    _seed_legacy_db(db)

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    for legacy in (
        "extraction_payload",
        "extraction_prompt_label",
        "prompt_sha256",
        "tokens_in",
        "tokens_out",
    ):
        assert legacy not in cols, f"legacy column {legacy!r} should be dropped"


def test_drops_legacy_prompt_label_index(tmp_path: Path):
    """The pre-existing idx_queue_items_prompt_label index must be removed —
    SQLite refuses DROP COLUMN on indexed columns, so the index drop is the
    prerequisite for the column drop."""
    db = tmp_path / "queue.db"
    _seed_legacy_db(db)

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        idxs = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='queue_items'"
            )
        }
    assert "idx_queue_items_prompt_label" not in idxs


def test_adds_cohort_columns_to_existing_db(tmp_path: Path):
    db = tmp_path / "queue.db"
    _seed_legacy_db(db)

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    for new_col in (
        "extractor_label",
        "extractor_sha256",
        "tokens_in_total",
        "tokens_out_total",
        "langfuse_trace_id",
    ):
        assert new_col in cols, f"current cohort column {new_col!r} should be present"


def test_creates_extraction_calls_table_when_missing(tmp_path: Path):
    db = tmp_path / "queue.db"
    _seed_legacy_db(db)

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extraction_calls" in tables


def test_preserves_cohort_id_and_fetch_data(tmp_path: Path):
    """The migration drops the legacy extraction columns but must NOT touch
    cohort identity (notion_page_id, url, canonical_url, content_type) or
    fetch state (raw_content, content_hash)."""
    db = tmp_path / "queue.db"
    _seed_legacy_db(db)

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT notion_page_id, url, canonical_url, content_type, "
            "raw_content, content_hash, extracted_at "
            "FROM queue_items WHERE notion_page_id = ?",
            ("p-legacy",),
        ).fetchone()
    assert row is not None
    assert row["notion_page_id"] == "p-legacy"
    assert row["url"] == "https://example.com/legacy"
    assert row["content_type"] == "Article"
    assert row["raw_content"] == "body"
    assert row["content_hash"] == "h"
    # extracted_at stays set; the operator must clear it (or re-launch the
    # partition) to re-extract under the three-call shape — that's the
    # deploy-step convention, not the script's job.
    assert row["extracted_at"] == "2026-06-01T00:00:00+00:00"


def test_idempotent_when_run_twice(tmp_path: Path):
    """Re-running on a migrated DB doesn't raise and doesn't change the shape."""
    db = tmp_path / "queue.db"
    _seed_legacy_db(db)

    migrate.main(db)
    migrate.main(db)

    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    assert "extraction_payload" not in cols
    assert "extractor_label" in cols
