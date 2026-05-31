"""Tests for domains.raw_store.queue — the queue_items SQLite layer.

Backs the deferred-learning queue pipeline (extract_queued_items). The schema
+ writes are owned by the orchestrator; NA reads via get_queue_extraction
against the same SQLite file in mode=ro.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from domains.raw_store.queue import (
    create_schema,
    get_queue_extraction,
    get_row,
    list_with_stale_extraction,
    update_extracted,
    upsert_fetched,
)


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


def test_create_schema_idempotent(tmp_path: Path):
    p = tmp_path / "q.db"
    create_schema(db_path=p)
    create_schema(db_path=p)
    with sqlite3.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "queue_items" in tables


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


def test_update_extracted_writes_topic_card(db_path: Path):
    _insert_fetched(db_path, page_id="p-1")
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={
            "extracted_title": "Anthropic on dreaming and memory",
            "core_mechanism": "Decoupling encoding from consolidation.",
            "best_example": "Sleep-time replay during slow-wave windows.",
            "second_example": "Targeted memory reactivation experiments.",
            "transferable_pattern": "Async consolidation of fast inputs.",
            "main_tension": "Throughput vs. consolidation fidelity.",
            "candidate_tie_backs": ["agent-memory", "personal-knowledge-os"],
        },
        prompt_label="v5_kp_copy_2026_05_31",
        prompt_sha256="sha-of-prompt",
        model="anthropic/claude-opus-4-7",
        tokens_in=4000,
        tokens_out=600,
    )
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row["extracted_title"] == "Anthropic on dreaming and memory"
    assert row["core_mechanism"].startswith("Decoupling")
    assert row["second_example"].startswith("Targeted")
    assert row["extraction_prompt_label"] == "v5_kp_copy_2026_05_31"
    assert row["extraction_model"] == "anthropic/claude-opus-4-7"
    assert row["prompt_sha256"] == "sha-of-prompt"
    assert row["tokens_in"] == 4000
    assert row["tokens_out"] == 600
    assert json.loads(row["candidate_tie_backs"]) == ["agent-memory", "personal-knowledge-os"]
    assert row["extracted_at"] is not None


def test_update_extracted_overwrites_prior_extraction(db_path: Path):
    """Load-bearing UPDATE-on-re-extract invariant (Section B / Section F).

    Locks Ladder C policy: bumping the prompt label overwrites the prior
    extraction. If this turns into INSERT, the production store grows a
    history-of-extractions surface that we explicitly opted out of building."""
    _insert_fetched(db_path, page_id="p-1")
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={
            "extracted_title": "v4 title",
            "core_mechanism": "v4 mech",
            "best_example": None,
            "second_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
        prompt_label="v4_kp_copy_2026_05_30",
        prompt_sha256="v4-sha",
        model="anthropic/claude-opus-4-7",
        tokens_in=3000,
        tokens_out=500,
    )
    update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={
            "extracted_title": "v5 title",
            "core_mechanism": "v5 mech",
            "best_example": "v5 example",
            "second_example": "v5 second",
            "transferable_pattern": "v5 pattern",
            "main_tension": "v5 tension",
            "candidate_tie_backs": ["t1"],
        },
        prompt_label="v5_kp_copy_2026_05_31",
        prompt_sha256="v5-sha",
        model="anthropic/claude-opus-4-7",
        tokens_in=3500,
        tokens_out=700,
    )
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row["extracted_title"] == "v5 title"
    assert row["core_mechanism"] == "v5 mech"
    assert row["extraction_prompt_label"] == "v5_kp_copy_2026_05_31"
    assert row["prompt_sha256"] == "v5-sha"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE notion_page_id=?", ("p-1",)
        ).fetchone()[0]
    assert count == 1, "UPDATE-on-re-extract must keep one row per page_id"


def test_get_row_returns_none_when_absent(db_path: Path):
    assert get_row(db_path=db_path, notion_page_id="missing") is None


def test_list_with_stale_extraction_filters_by_age(db_path: Path):
    _insert_fetched(db_path, page_id="recent")
    _insert_fetched(db_path, page_id="stale", url="https://example.com/b")
    now = datetime.now(UTC)
    update_extracted(
        db_path=db_path,
        notion_page_id="recent",
        extraction={
            "extracted_title": "x",
            "core_mechanism": None,
            "best_example": None,
            "second_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
        prompt_label="v5",
        prompt_sha256="s",
        model="m",
        tokens_in=1,
        tokens_out=1,
    )
    update_extracted(
        db_path=db_path,
        notion_page_id="stale",
        extraction={
            "extracted_title": "y",
            "core_mechanism": None,
            "best_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
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
        extraction={
            "extracted_title": "x",
            "core_mechanism": None,
            "best_example": None,
            "second_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
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


def test_get_queue_extraction_returns_none_when_row_absent(db_path: Path):
    assert get_queue_extraction(db_path=db_path, notion_page_id="missing") is None


def test_get_queue_extraction_returns_none_when_fetched_but_not_extracted(db_path: Path):
    _insert_fetched(db_path)
    assert get_queue_extraction(db_path=db_path, notion_page_id="p-1") is None


def test_get_queue_extraction_returns_topic_card_shape(db_path: Path):
    """Matches Section E1's contract — what NA reads on engagement."""
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
    assert out["url"] == "https://example.com/a"
    assert out["extracted_title"] == "Anthropic talk"
    assert out["candidate_tie_backs"] == ["agent-memory", "personal-knowledge-os"]
    assert out["extraction_prompt_label"] == "v5_kp_copy_2026_05_31"
    assert out["extraction_model"] == "anthropic/claude-opus-4-7"
    assert out["content_hash"] == "abc123"
    assert "raw_content" not in out, "NA consumer should not receive raw_content"
