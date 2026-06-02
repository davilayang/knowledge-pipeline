"""Tests for domains.queue_store.sources — the queue_items SQLite layer.

Backs the deferred-learning queue pipeline (extract_queued_items). The schema
+ writes are owned by the orchestrator; NA reads via get_queue_extraction
against the same SQLite file in mode=ro.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from domains.queue_store.sources import (
    create_schema,
    get_queue_extraction,
    get_row,
    list_with_stale_extraction,
    update_extracted,
    upsert_fetched,
    upsert_triaged,
)

_OLD_PR65_SCHEMA = """
CREATE TABLE queue_items (
    notion_page_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    raw_content TEXT,
    fetched_at TEXT,
    fetch_tier TEXT,
    fetch_tier_log TEXT,
    fetched_content_char_count INTEGER,
    content_hash TEXT,
    extracted_title TEXT,
    core_mechanism TEXT,
    best_example TEXT,
    second_example TEXT,
    transferable_pattern TEXT,
    main_tension TEXT,
    candidate_tie_backs TEXT,
    extraction_prompt_label TEXT,
    extraction_model TEXT,
    prompt_sha256 TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    extracted_at TEXT,
    error_text TEXT
);
"""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "queue.db"
    create_schema(db_path=p)
    return p


def _insert_fetched(
    db_path: Path, page_id: str = "p-1", url: str = "https://example.com/a"
) -> None:
    upsert_fetched(
        db_path=db_path,
        notion_page_id=page_id,
        url=url,
        raw_content="raw markdown body, long enough to be useful.",
        fetch_tier="jina",
        fetch_tier_log=[{"tier": "jina", "status": 200, "chars": 5000}],
        fetched_content_char_count=5000,
        content_hash="abc123",
    )


# --- Schema tests ---


def test_create_schema_idempotent(tmp_path: Path):
    p = tmp_path / "q.db"
    create_schema(db_path=p)
    create_schema(db_path=p)
    with sqlite3.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "queue_items" in tables


def test_create_schema_adds_new_columns(tmp_path: Path):
    p = tmp_path / "q.db"
    create_schema(db_path=p)
    with sqlite3.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    assert "canonical_url" in cols
    assert "content_type" in cols
    assert "extraction_payload" in cols
    # Old per-field Topic Card columns must be absent
    for old_col in (
        "extracted_title",
        "core_mechanism",
        "best_example",
        "second_example",
        "transferable_pattern",
        "main_tension",
        "candidate_tie_backs",
    ):
        assert old_col not in cols, f"old column {old_col!r} should not exist in new schema"


def test_create_schema_is_idempotent_on_pre_existing_db_from_pr65(tmp_path: Path):
    """create_schema() must forward-migrate a PR #65 DB without error.

    Old per-field columns linger (unused); the three new columns are added.
    """
    p = tmp_path / "old.db"
    # Bootstrap a DB with the old PR #65 schema directly.
    with sqlite3.connect(p) as conn:
        conn.executescript(_OLD_PR65_SCHEMA)

    # Running create_schema on it must not raise.
    create_schema(db_path=p)

    with sqlite3.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}

    # New columns now present.
    assert "canonical_url" in cols
    assert "content_type" in cols
    assert "extraction_payload" in cols
    # Old per-field columns still present (they linger as unused).
    assert "extracted_title" in cols
    assert "core_mechanism" in cols


# --- upsert_fetched tests ---


def test_upsert_fetched_inserts_new_row(db_path: Path):
    _insert_fetched(db_path, page_id="p-1")
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row is not None
    assert row["url"] == "https://example.com/a"
    assert row["raw_content"].startswith("raw markdown")
    assert row["fetch_tier"] == "jina"
    assert row["fetched_content_char_count"] == 5000
    assert row["content_hash"] == "abc123"
    assert row["extracted_at"] is None


def test_upsert_fetched_updates_existing_row(db_path: Path):
    _insert_fetched(db_path, page_id="p-1")
    upsert_fetched(
        db_path=db_path,
        notion_page_id="p-1",
        url="https://example.com/a",
        raw_content="fresh body after re-fetch",
        fetch_tier="curl_cffi",
        fetch_tier_log=[{"tier": "jina", "status": 403}, {"tier": "curl_cffi", "status": 200}],
        fetched_content_char_count=8000,
        content_hash="def456",
    )
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row["raw_content"] == "fresh body after re-fetch"
    assert row["fetch_tier"] == "curl_cffi"
    assert row["content_hash"] == "def456"


# --- upsert_triaged tests ---


def test_upsert_triaged_inserts_with_content_type(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-1",
        url="https://example.com/article",
        canonical_url="https://example.com/article",
        content_type="article",
    )
    row = get_row(db_path=db_path, notion_page_id="t-1")
    assert row is not None
    assert row["url"] == "https://example.com/article"
    assert row["canonical_url"] == "https://example.com/article"
    assert row["content_type"] == "article"
    assert row["error_text"] is None


def test_upsert_triaged_updates_existing_row_on_url_change(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-1",
        url="https://example.com/old",
        canonical_url="https://example.com/old",
        content_type="article",
    )
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-1",
        url="https://example.com/new",
        canonical_url="https://example.com/new",
        content_type="youtube",
    )
    row = get_row(db_path=db_path, notion_page_id="t-1")
    assert row["url"] == "https://example.com/new"
    assert row["canonical_url"] == "https://example.com/new"
    assert row["content_type"] == "youtube"
    # Must still be a single row.
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE notion_page_id=?", ("t-1",)
        ).fetchone()[0]
    assert count == 1


# --- update_extracted tests ---


def test_update_extracted_stores_payload_as_json(db_path: Path):
    _insert_fetched(db_path, page_id="p-1")
    topic_card = {
        "extracted_title": "Anthropic on dreaming and memory",
        "core_mechanism": "Decoupling encoding from consolidation.",
        "candidate_tie_backs": ["agent-memory", "personal-knowledge-os"],
    }
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction=topic_card,
        prompt_label="v5_kp_copy_2026_05_31",
        prompt_sha256="sha-of-prompt",
        model="anthropic/claude-opus-4-7",
        tokens_in=4000,
        tokens_out=600,
    )
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute(
            "SELECT extraction_payload FROM queue_items WHERE notion_page_id=?", ("p-1",)
        ).fetchone()[0]
    assert json.loads(raw) == topic_card


def test_update_extracted_overwrites_payload_on_re_extract(db_path: Path):
    """UPDATE-on-re-extract policy: bumping prompt label overwrites prior extraction."""
    _insert_fetched(db_path, page_id="p-1")
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={"extracted_title": "v4 title", "core_mechanism": "v4 mech"},
        prompt_label="v4_kp_copy_2026_05_30",
        prompt_sha256="v4-sha",
        model="anthropic/claude-opus-4-7",
        tokens_in=3000,
        tokens_out=500,
    )
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={"extracted_title": "v5 title", "core_mechanism": "v5 mech"},
        prompt_label="v5_kp_copy_2026_05_31",
        prompt_sha256="v5-sha",
        model="anthropic/claude-opus-4-7",
        tokens_in=3500,
        tokens_out=700,
    )
    row = get_row(db_path=db_path, notion_page_id="p-1")
    payload = json.loads(row["extraction_payload"])
    assert payload["extracted_title"] == "v5 title"
    assert payload["core_mechanism"] == "v5 mech"
    assert row["extraction_prompt_label"] == "v5_kp_copy_2026_05_31"
    assert row["prompt_sha256"] == "v5-sha"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE notion_page_id=?", ("p-1",)
        ).fetchone()[0]
    assert count == 1, "UPDATE-on-re-extract must keep one row per page_id"


def test_update_extracted_handles_heterogeneous_payload_shape(db_path: Path):
    """arXiv-shaped payload stores and round-trips without schema changes."""
    _insert_fetched(db_path, page_id="p-arxiv")
    arxiv_payload = {"authors": ["Alice", "Bob"], "abstract": "A novel approach to X."}
    update_extracted(
        db_path=db_path,
        notion_page_id="p-arxiv",
        extraction=arxiv_payload,
        prompt_label="arxiv_v1",
        prompt_sha256="arxiv-sha",
        model="anthropic/claude-opus-4-7",
        tokens_in=2000,
        tokens_out=400,
    )
    out = get_queue_extraction(db_path=db_path, notion_page_id="p-arxiv")
    assert out is not None
    assert out["authors"] == ["Alice", "Bob"]
    assert out["abstract"] == "A novel approach to X."
    assert out["extraction_prompt_label"] == "arxiv_v1"


# --- get_queue_extraction tests ---


def test_get_queue_extraction_returns_none_when_row_absent(db_path: Path):
    assert get_queue_extraction(db_path=db_path, notion_page_id="missing") is None


def test_get_queue_extraction_returns_none_when_extracted_at_is_null(db_path: Path):
    _insert_fetched(db_path)
    assert get_queue_extraction(db_path=db_path, notion_page_id="p-1") is None


def test_get_queue_extraction_flattens_payload_with_provenance(db_path: Path):
    """Payload keys and provenance keys are both top-level in the returned dict."""
    _insert_fetched(db_path)
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={
            "extracted_title": "Anthropic talk",
            "core_mechanism": "Sleep-time consolidation",
            "best_example": "Replay during SWS",
            "transferable_pattern": "Async memory consolidation",
            "main_tension": "Throughput vs fidelity",
            "candidate_tie_backs": ["agent-memory", "personal-knowledge-os"],
        },
        prompt_label="v5_kp_copy_2026_05_31",
        prompt_sha256="sha-x",
        model="anthropic/claude-opus-4-7",
        tokens_in=4000,
        tokens_out=600,
    )
    out = get_queue_extraction(db_path=db_path, notion_page_id="p-1")
    assert out is not None
    # Payload keys flattened to top level.
    assert out["extracted_title"] == "Anthropic talk"
    assert out["candidate_tie_backs"] == ["agent-memory", "personal-knowledge-os"]
    # Provenance keys also top-level.
    assert out["extraction_prompt_label"] == "v5_kp_copy_2026_05_31"
    assert out["extraction_model"] == "anthropic/claude-opus-4-7"
    assert out["extracted_at"] is not None
    assert out["content_hash"] == "abc123"
    assert out["url"] == "https://example.com/a"
    assert "raw_content" not in out, "NA consumer should not receive raw_content"


# --- get_row / mark_failed tests ---


def test_get_row_returns_none_when_absent(db_path: Path):
    assert get_row(db_path=db_path, notion_page_id="missing") is None


# --- list_with_stale_extraction tests ---


def test_list_with_stale_extraction_filters_by_age(db_path: Path):
    _insert_fetched(db_path, page_id="recent")
    _insert_fetched(db_path, page_id="stale", url="https://example.com/b")
    now = datetime.now(UTC)
    update_extracted(
        db_path=db_path,
        notion_page_id="recent",
        extraction={"extracted_title": "x"},
        prompt_label="v5",
        prompt_sha256="s",
        model="m",
        tokens_in=1,
        tokens_out=1,
    )
    update_extracted(
        db_path=db_path,
        notion_page_id="stale",
        extraction={"extracted_title": "y"},
        prompt_label="v5",
        prompt_sha256="s",
        model="m",
        tokens_in=1,
        tokens_out=1,
    )
    # Backdate the stale row by 2 hours.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE queue_items SET extracted_at=? WHERE notion_page_id=?",
            ((now - timedelta(hours=2)).isoformat(), "stale"),
        )

    stale_rows = list_with_stale_extraction(db_path=db_path, min_age_minutes=60)
    assert {r["notion_page_id"] for r in stale_rows} == {"stale"}


def test_list_with_stale_extraction_does_not_select_raw_content(db_path: Path):
    """Lightweight scan — must not pull the 5MB raw_content column."""
    _insert_fetched(db_path)
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={"extracted_title": "x"},
        prompt_label="v5",
        prompt_sha256="s",
        model="m",
        tokens_in=1,
        tokens_out=1,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE queue_items SET extracted_at=? WHERE notion_page_id=?",
            ((datetime.now(UTC) - timedelta(hours=2)).isoformat(), "p-1"),
        )
    rows = list_with_stale_extraction(db_path=db_path, min_age_minutes=60)
    assert rows and "raw_content" not in rows[0]
