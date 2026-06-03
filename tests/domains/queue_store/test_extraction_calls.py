"""Tests for the extraction_calls table + record_extraction_calls writer +
get_latest_extraction_calls reader.

extraction_calls mirrors NA's `core_llm_calls` shape — one row per LLM call
with output + provenance side-by-side. Multiple rows per (notion_page_id,
call_kind) are allowed (LangGraph refinement loops); the reader returns the
most-recent per call_kind.

The existing extraction_payload column on queue_items is intentionally NOT
dropped in this phase — kept for one release cycle for cheap rollback per the
plan's §Rollback recommendation.
"""

from pathlib import Path

import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.queue_store.sources import (
    create_schema,
    get_latest_extraction_calls,
    get_row,
    record_extraction_calls,
    upsert_fetched,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "queue.db"
    create_schema(db_path=p)
    upsert_fetched(
        db_path=p,
        notion_page_id="p-1",
        url="https://example.com/a",
        raw_content="body",
        fetch_tier="curl_cffi",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="x",
    )
    return p


def _call(call_kind: str, output: str, **overrides) -> ExtractionCallRecord:
    base = dict(
        call_kind=call_kind,
        prompt_label=f"{call_kind}_v1",
        prompt_sha256=f"{call_kind}_sha",
        schema_name=None if call_kind == "narrative" else call_kind.title().replace("_", ""),
        output=output,
        tokens_in=100,
        tokens_out=50,
        cached_tokens=80,
        duration_ms=1234.5,
        extracted_at="2026-06-03T12:00:00+00:00",
        node_metadata=None,
    )
    base.update(overrides)
    return ExtractionCallRecord(**base)


def test_record_extraction_calls_inserts_one_row_per_call(db_path: Path):
    record_extraction_calls(
        db_path=db_path,
        notion_page_id="p-1",
        extractor_label="3call_v1",
        extractor_sha256="bundle_sha",
        model="gpt-4.1-mini",
        calls=[
            _call("narrative", "# narrative body"),
            _call("topic_card", '{"extracted_title": "t"}'),
            _call("followups", '{"questions": ["a?","b?","c?","d?"]}'),
        ],
        tokens_in_total=300,
        tokens_out_total=150,
    )
    latest = get_latest_extraction_calls(db_path=db_path, notion_page_id="p-1")
    assert set(latest.keys()) == {"narrative", "topic_card", "followups"}
    assert latest["narrative"]["output"] == "# narrative body"
    assert latest["topic_card"]["output"] == '{"extracted_title": "t"}'


def test_record_extraction_calls_updates_queue_items_cohort_fields(db_path: Path):
    record_extraction_calls(
        db_path=db_path,
        notion_page_id="p-1",
        extractor_label="3call_v1",
        extractor_sha256="bundle_sha",
        model="gpt-4.1-mini",
        calls=[
            _call("narrative", "n"),
            _call("topic_card", "t"),
            _call("followups", "f"),
        ],
        tokens_in_total=300,
        tokens_out_total=150,
        langfuse_trace_id="trace-abc",
    )
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row is not None
    assert row["extracted_at"] == "2026-06-03T12:00:00+00:00"
    assert row["extractor_label"] == "3call_v1"
    assert row["extractor_sha256"] == "bundle_sha"
    assert row["extraction_model"] == "gpt-4.1-mini"
    assert row["tokens_in_total"] == 300
    assert row["tokens_out_total"] == 150
    assert row["langfuse_trace_id"] == "trace-abc"
    assert row["error_text"] is None


def test_record_extraction_calls_sets_extracted_at_to_max_call_timestamp(db_path: Path):
    record_extraction_calls(
        db_path=db_path,
        notion_page_id="p-1",
        extractor_label="3call_v1",
        extractor_sha256="bundle_sha",
        model="gpt-4.1-mini",
        calls=[
            _call("narrative", "n", extracted_at="2026-06-03T12:00:00+00:00"),
            _call("topic_card", "t", extracted_at="2026-06-03T12:00:05+00:00"),
            _call("followups", "f", extracted_at="2026-06-03T12:00:03+00:00"),
        ],
        tokens_in_total=300,
        tokens_out_total=150,
    )
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row is not None
    assert row["extracted_at"] == "2026-06-03T12:00:05+00:00"


def test_get_latest_extraction_calls_returns_most_recent_per_kind(db_path: Path):
    """LangGraph refinement loops can write multiple rows per call_kind; reader
    must return the most-recent. Older rows stay as audit trail."""
    record_extraction_calls(
        db_path=db_path,
        notion_page_id="p-1",
        extractor_label="3call_v1",
        extractor_sha256="bundle_sha_v1",
        model="gpt-4.1-mini",
        calls=[
            _call(
                "topic_card",
                '{"extracted_title": "old"}',
                prompt_label="topic_card_v1",
                extracted_at="2026-06-03T12:00:00+00:00",
            ),
        ],
        tokens_in_total=100,
        tokens_out_total=50,
    )
    record_extraction_calls(
        db_path=db_path,
        notion_page_id="p-1",
        extractor_label="3call_v2",
        extractor_sha256="bundle_sha_v2",
        model="gpt-4.1-mini",
        calls=[
            _call(
                "topic_card",
                '{"extracted_title": "new"}',
                prompt_label="topic_card_v2",
                extracted_at="2026-06-03T13:00:00+00:00",
            ),
        ],
        tokens_in_total=100,
        tokens_out_total=50,
    )
    latest = get_latest_extraction_calls(db_path=db_path, notion_page_id="p-1")
    assert latest["topic_card"]["output"] == '{"extracted_title": "new"}'
    assert latest["topic_card"]["prompt_label"] == "topic_card_v2"


def test_get_latest_extraction_calls_returns_empty_for_unknown_page(db_path: Path):
    assert get_latest_extraction_calls(db_path=db_path, notion_page_id="never-seen") == {}


def test_create_schema_idempotent_for_extraction_calls_columns(db_path: Path):
    """Re-running create_schema on a DB that already has the new columns is a
    no-op (no `duplicate column` error escapes)."""
    create_schema(db_path=db_path)
    create_schema(db_path=db_path)
