"""Tests for the prod migration script.

Idempotency + correctness on a fresh DB and on a DB pre-seeded with legacy
single-shot rows. The script is the artefact under test — exercise the
public entry point (`main`)."""

import sqlite3
from pathlib import Path

import pytest
from domains.queue_store.sources import create_schema, update_extracted, upsert_fetched

import scripts.migrate_extraction_to_calls_table as migrate


def _seed_legacy_row(db_path: Path, page_id: str = "p-legacy"):
    """Pretend prod state: a row with old-shape extraction_payload + per-call
    columns populated, no extraction_calls rows yet."""
    upsert_fetched(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/legacy",
        raw_content="body",
        fetch_tier="curl_cffi",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="hh",
    )
    update_extracted(
        db_path=db_path,
        notion_page_id=page_id,
        extraction={"extracted_title": "Legacy Title", "core_mechanism": "old mech"},
        prompt_label="v5_article_kp_copy_2026_05_31",
        prompt_sha256="a" * 64,
        model="gpt-4o-mini",
        tokens_in=1000,
        tokens_out=500,
    )


def test_main_returns_0_on_existing_db(tmp_path: Path):
    db = tmp_path / "queue.db"
    create_schema(db_path=db)
    assert migrate.main(db) == 0


def test_main_returns_1_when_db_missing(tmp_path: Path):
    db = tmp_path / "missing.db"
    assert migrate.main(db) == 1


def test_seeds_legacy_row_into_extraction_calls(tmp_path: Path):
    db = tmp_path / "queue.db"
    create_schema(db_path=db)
    _seed_legacy_row(db, "p-legacy")

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT call_kind, prompt_label, output, tokens_in, tokens_out, extracted_at "
            "FROM extraction_calls WHERE notion_page_id = ?",
            ("p-legacy",),
        ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["call_kind"] == "legacy_v5"
    assert row["prompt_label"] == "v5_article_kp_copy_2026_05_31"
    assert "Legacy Title" in row["output"]
    assert row["tokens_in"] == 1000
    assert row["tokens_out"] == 500


def test_updates_cohort_columns_on_legacy_row(tmp_path: Path):
    db = tmp_path / "queue.db"
    create_schema(db_path=db)
    _seed_legacy_row(db, "p-legacy")

    migrate.main(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT extractor_label, extractor_sha256, tokens_in_total, tokens_out_total "
            "FROM queue_items WHERE notion_page_id = ?",
            ("p-legacy",),
        ).fetchone()
    assert row["extractor_label"] == "legacy_v5"
    assert row["extractor_sha256"] == "a" * 64
    assert row["tokens_in_total"] == 1000
    assert row["tokens_out_total"] == 500


def test_idempotent_when_run_twice(tmp_path: Path):
    """Re-running on an already-migrated DB doesn't duplicate rows."""
    db = tmp_path / "queue.db"
    create_schema(db_path=db)
    _seed_legacy_row(db, "p-legacy")

    migrate.main(db)
    migrate.main(db)

    with sqlite3.connect(db) as conn:
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM extraction_calls WHERE notion_page_id = ?",
            ("p-legacy",),
        ).fetchone()[0]
    assert n_rows == 1


def test_skips_rows_without_extraction_payload(tmp_path: Path):
    """A row that's only been triaged (no extraction yet) gets no seed row."""
    db = tmp_path / "queue.db"
    create_schema(db_path=db)
    # Just upsert_fetched — no update_extracted, so extraction_payload is NULL.
    upsert_fetched(
        db_path=db,
        notion_page_id="p-fresh",
        url="https://example.com/fresh",
        raw_content="body",
        fetch_tier="curl_cffi",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="h",
    )
    migrate.main(db)

    with sqlite3.connect(db) as conn:
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM extraction_calls WHERE notion_page_id = ?",
            ("p-fresh",),
        ).fetchone()[0]
    assert n_rows == 0


@pytest.fixture(autouse=True)
def _add_repo_root_to_sys_path():
    """The migration script imports `domains.queue_store.sources` at runtime;
    the test fixture context already has it on sys.path via uv workspaces."""
    yield
