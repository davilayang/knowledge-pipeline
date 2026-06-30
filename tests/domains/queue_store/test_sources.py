"""Tests for domains.queue_store.sources — the queue_items SQLite layer.

Backs the deferred-learning queue pipeline (fetch_extract_queue). The
schema + writes are owned by the orchestrator; NA reads via
`get_queue_extraction` against the same SQLite file in mode=ro.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.queue_store.sources import (
    checkpoint_wal,
    create_schema,
    find_canonical_url_duplicate,
    get_content_shape,
    get_latest_extraction_calls,
    get_queue_extraction,
    get_row,
    get_source_summary,
    list_with_stale_extraction,
    record_extraction_calls,
    record_source_summary,
    upsert_enriched,
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


def _call(call_kind: str, output: str, **overrides) -> ExtractionCallRecord:
    base = dict(
        call_kind=call_kind,
        prompt_label=f"{call_kind}_v1",
        prompt_sha256=f"{call_kind}_sha".ljust(64, "0"),
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


def _record_three_call(db_path: Path, page_id: str = "p-1", **overrides):
    """Helper: write a complete three-call cohort for the given page."""
    topic_payload = overrides.pop(
        "topic_payload",
        {
            "extracted_title": "T",
            "core_mechanism": "M",
            "best_example": "E",
            "transferable_pattern": "P",
            "main_tension": "X",
            "candidate_tie_backs": [],
        },
    )
    calls = [
        _call("narrative", "# narrative"),
        _call("topic_card", json.dumps(topic_payload)),
        _call("followups", json.dumps({"questions": ["a?", "b?", "c?", "d?"]})),
    ]
    record_extraction_calls(
        db_path=db_path,
        notion_page_id=page_id,
        extractor_label=overrides.get("extractor_label", "3call_v1"),
        extractor_sha256=overrides.get("extractor_sha256", "b" * 64),
        model=overrides.get("model", "gpt-4o-mini"),
        calls=calls,
        tokens_in_total=overrides.get("tokens_in_total", 300),
        tokens_out_total=overrides.get("tokens_out_total", 150),
        langfuse_trace_id=overrides.get("langfuse_trace_id"),
    )


# --- Schema tests ---


def test_create_schema_idempotent(tmp_path: Path):
    p = tmp_path / "q.db"
    create_schema(db_path=p)
    create_schema(db_path=p)
    with sqlite3.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "queue_items" in tables
    assert "extraction_calls" in tables


def test_fresh_schema_has_expected_columns_and_no_legacy(tmp_path: Path):
    p = tmp_path / "q.db"
    create_schema(db_path=p)
    with sqlite3.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    # Current cohort columns present.
    for col in (
        "canonical_url",
        "content_type",
        "extractor_label",
        "extractor_sha256",
        "tokens_in_total",
        "tokens_out_total",
        "langfuse_trace_id",
    ):
        assert col in cols, f"expected current column {col!r} missing"
    # Legacy single-shot columns absent.
    for col in (
        "extraction_payload",
        "extraction_prompt_label",
        "prompt_sha256",
        "tokens_in",
        "tokens_out",
    ):
        assert col not in cols, f"legacy column {col!r} should be dropped from fresh schema"
    # Old per-field Topic Card columns from PR #65 also absent.
    for col in (
        "extracted_title",
        "core_mechanism",
        "best_example",
        "second_example",
        "transferable_pattern",
        "main_tension",
        "candidate_tie_backs",
    ):
        assert col not in cols, f"old per-field column {col!r} should not exist in new schema"


def test_create_schema_forward_migrates_pr65_db(tmp_path: Path):
    """create_schema() must forward-migrate a PR #65 DB without error.

    The old per-field Topic Card columns linger (DROP COLUMN is only run on
    the named legacy single-shot columns; per-field columns are pre-PR-#65
    and not handled here). The current cohort columns are added.
    """
    p = tmp_path / "old.db"
    with sqlite3.connect(p) as conn:
        conn.executescript(_OLD_PR65_SCHEMA)

    create_schema(db_path=p)

    with sqlite3.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
    assert "canonical_url" in cols
    assert "content_type" in cols
    assert "extractor_label" in cols
    # Legacy single-shot columns dropped.
    assert "extraction_payload" not in cols
    assert "extraction_prompt_label" not in cols
    assert "prompt_sha256" not in cols


def test_create_schema_drops_legacy_single_shot_columns(tmp_path: Path):
    """A DB carrying the v1-three-call-rollout intermediate shape (with
    extraction_payload + idx_queue_items_prompt_label both present) must
    converge to the current shape without manual intervention."""
    p = tmp_path / "single_shot.db"
    with sqlite3.connect(p) as conn:
        conn.executescript(_LEGACY_SINGLE_SHOT_SCHEMA)

    create_schema(db_path=p)

    with sqlite3.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(queue_items)")}
        idxs = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='queue_items'"
            )
        }
    for legacy in (
        "extraction_payload",
        "extraction_prompt_label",
        "prompt_sha256",
        "tokens_in",
        "tokens_out",
    ):
        assert legacy not in cols
    assert "idx_queue_items_prompt_label" not in idxs


# --- upsert_fetched tests ---


def test_upsert_fetched_inserts_new_row(db_path: Path):
    _insert_fetched(db_path, page_id="p-1")
    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row is not None
    assert row["url"] == "https://example.com/a"
    assert row["raw_content"].startswith("raw markdown")


def test_upsert_fetched_persists_title_author_content_date(db_path: Path):
    # The fetcher returns title/author/content_date; persisting them on the queue
    # row makes it self-sufficient for source summarisation (no raw_store join).
    upsert_fetched(
        db_path=db_path,
        notion_page_id="p-meta",
        url="https://example.com/a",
        raw_content="body",
        fetch_tier="jina",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="h",
        title="Real Title",
        author="Jane Doe",
        content_date="2026-06-01",
    )
    row = get_row(db_path=db_path, notion_page_id="p-meta")
    assert row["title"] == "Real Title"
    assert row["author"] == "Jane Doe"
    assert row["content_date"] == "2026-06-01"


def test_upsert_fetched_round_trips_fetch_tier_log(db_path: Path):
    upsert_fetched(
        db_path=db_path,
        notion_page_id="p-tier",
        url="https://example.com/x",
        raw_content="x",
        fetch_tier="curl_cffi",
        fetch_tier_log=[
            {"tier": "jina", "status": 200, "chars": 50},
            {"tier": "curl_cffi", "status": 200, "chars": 5000},
        ],
        fetched_content_char_count=5000,
        content_hash="h",
    )
    row = get_row(db_path=db_path, notion_page_id="p-tier")
    assert row is not None
    log = json.loads(row["fetch_tier_log"])
    assert log[0]["tier"] == "jina"
    assert log[1]["chars"] == 5000


def test_upsert_triaged_round_trips_canonical_and_content_type(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-1",
        url="https://youtube.com/watch?v=x&utm_source=newsletter",
        canonical_url="https://youtube.com/watch?v=x",
        content_type="youtube",
    )
    row = get_row(db_path=db_path, notion_page_id="t-1")
    assert row is not None
    assert row["canonical_url"] == "https://youtube.com/watch?v=x"
    assert row["content_type"] == "youtube"


def test_upsert_triaged_persists_raw_content_override(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-2",
        url="https://example.com/a",
        canonical_url="https://example.com/a",
        content_type="Article",
        raw_content_override="# pasted body\n\nlong content...",
    )
    row = get_row(db_path=db_path, notion_page_id="t-2")
    assert row is not None
    assert row["raw_content_override"] == "# pasted body\n\nlong content..."


def test_upsert_triaged_defaults_raw_content_override_to_empty(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-3",
        url="https://example.com/b",
        canonical_url="https://example.com/b",
        content_type="Article",
    )
    row = get_row(db_path=db_path, notion_page_id="t-3")
    assert row is not None
    assert row["raw_content_override"] == ""


def test_find_canonical_url_duplicate_returns_none_when_empty(db_path: Path):
    assert (
        find_canonical_url_duplicate(
            db_path=db_path,
            canonical_url="https://example.com/x",
            excluding_page_id="p-new",
        )
        is None
    )


def test_find_canonical_url_duplicate_excludes_self(db_path: Path):
    """Re-triaging the same Notion page shouldn't flag its own row as a dup."""
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p-1",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
    )
    assert (
        find_canonical_url_duplicate(
            db_path=db_path,
            canonical_url="https://example.com/x",
            excluding_page_id="p-1",
        )
        is None
    )


def test_find_canonical_url_duplicate_returns_other_page_id(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p-1",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
    )
    assert (
        find_canonical_url_duplicate(
            db_path=db_path,
            canonical_url="https://example.com/x",
            excluding_page_id="p-2",
        )
        == "p-1"
    )


def test_find_canonical_url_duplicate_picks_oldest_when_multiple(db_path: Path):
    """Stable forensics: with multiple matches, return the earliest-inserted row."""
    for pid in ("p-1", "p-2", "p-3"):
        upsert_triaged(
            db_path=db_path,
            notion_page_id=pid,
            url="https://example.com/x",
            canonical_url="https://example.com/x",
            content_type="Article",
        )
    assert (
        find_canonical_url_duplicate(
            db_path=db_path,
            canonical_url="https://example.com/x",
            excluding_page_id="p-4",
        )
        == "p-1"
    )


def test_find_canonical_url_duplicate_ignores_other_urls(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p-1",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
    )
    assert (
        find_canonical_url_duplicate(
            db_path=db_path,
            canonical_url="https://example.com/y",
            excluding_page_id="p-2",
        )
        is None
    )


def test_schema_includes_content_shape_columns(db_path: Path):
    """queue_items carries content_shape (extractor routing) + enrichment_json
    (signals cache) alongside content_type. NULL until triage/enrich
    materialise — `"unknown"` is the consumer-facing coalesce, not a column
    default."""
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(queue_items)")}
    assert "content_shape" in cols
    assert "enrichment_json" in cols


def test_create_schema_idempotent_with_content_shape_columns(tmp_path: Path):
    """Second call to create_schema on a DB that already has content_shape /
    enrichment_json must not raise — ALTER TABLE ADD COLUMN reruns are the
    failure mode this guards against on prod redeploys."""
    p = tmp_path / "q.db"
    create_schema(db_path=p)
    create_schema(db_path=p)
    with sqlite3.connect(p) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(queue_items)")}
    assert "content_shape" in cols
    assert "enrichment_json" in cols


def test_upsert_enriched_stores_json_and_overwrites_on_conflict(db_path: Path):
    """`enriched` asset is idempotent by page_id — re-materialising overwrites
    the prior enrichment_json. No append-only growth, no orphan rows for the
    same page_id."""
    page_id = "p-enrich"
    upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
    )
    upsert_enriched(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/x",
        enrichment_json='{"a":1}',
    )
    upsert_enriched(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/x",
        enrichment_json='{"a":2}',
    )
    row = get_row(db_path=db_path, notion_page_id=page_id)
    assert row is not None
    assert row["enrichment_json"] == '{"a":2}'


def test_upsert_enriched_creates_row_when_no_triaged_yet(db_path: Path):
    """`enriched` runs before `triaged` in the asset graph, so `upsert_enriched`
    must create the row with the captured URL (not an empty placeholder). When
    `triaged` lands after, ON CONFLICT overwrites the identity columns it owns
    (url / canonical_url / content_type) while enrichment_json survives."""
    page_id = "p-enrich-first"
    upsert_enriched(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/raw",
        enrichment_json='{"a":1}',
    )
    pre = get_row(db_path=db_path, notion_page_id=page_id)
    assert pre is not None
    assert pre["url"] == "https://example.com/raw"
    assert pre["enrichment_json"] == '{"a":1}'
    assert pre["canonical_url"] is None
    assert pre["content_type"] is None

    upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/raw",
        canonical_url="https://example.com/raw",
        content_type="Article",
    )
    post = get_row(db_path=db_path, notion_page_id=page_id)
    assert post is not None
    assert post["url"] == "https://example.com/raw"
    assert post["canonical_url"] == "https://example.com/raw"
    assert post["content_type"] == "Article"
    # enrichment_json survives the re-triage write.
    assert post["enrichment_json"] == '{"a":1}'


def test_upsert_triaged_accepts_content_shape(db_path: Path):
    """`upsert_triaged` accepts `content_shape` and lands it on the row."""
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p-cs",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
        content_shape="opinion_essay",
    )
    row = get_row(db_path=db_path, notion_page_id="p-cs")
    assert row is not None
    assert row["content_shape"] == "opinion_essay"


def test_get_content_shape_coalesces_null_to_unknown(db_path: Path):
    """Rows triaged before content_shape landed carry NULL. Consumers MUST
    see `"unknown"` so the extractor's per-shape prompt lookup falls through
    to the generic fallback bundle instead of KeyError'ing on None."""
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p-null",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
    )
    # Sanity: column was left NULL since no content_shape was passed.
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute(
            "SELECT content_shape FROM queue_items WHERE notion_page_id=?",
            ("p-null",),
        ).fetchone()[0]
    assert raw is None

    assert get_content_shape(db_path=db_path, notion_page_id="p-null") == "unknown"


def test_get_content_shape_returns_unknown_for_missing_page(db_path: Path):
    assert get_content_shape(db_path=db_path, notion_page_id="missing") == "unknown"


def test_get_content_shape_returns_stored_value_when_set(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p-set",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
        content_shape="tutorial",
    )
    assert get_content_shape(db_path=db_path, notion_page_id="p-set") == "tutorial"


def test_upsert_triaged_clears_stale_fetch_and_extraction_state(db_path: Path):
    """Re-triage is a cohort boundary: a row that already has fetched +
    extracted state from a prior cycle must lose all of it on re-triage so
    the `fetched` cache check doesn't short-circuit on stale content (the
    Medium-handler PR #109 incident shape) and `get_queue_extraction` does
    not serve last-cohort Topic Cards while the row is back at Fetching."""
    page_id = "t-stale"
    upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="Article",
    )
    upsert_fetched(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/x",
        raw_content="raw markdown body, long enough to be useful.",
        fetch_tier="jina",
        fetch_tier_log=[{"tier": "jina", "status": 200, "chars": 5000}],
        fetched_content_char_count=5000,
        content_hash="abc123",
        title="Stale Title",
        author="Stale Author",
        content_date="2026-06-01",
    )
    _record_three_call(db_path, page_id=page_id)

    pre = get_row(db_path=db_path, notion_page_id=page_id)
    assert pre is not None
    assert pre["raw_content"] is not None
    assert pre["extracted_at"] is not None
    assert pre["title"] == "Stale Title"

    upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/x?utm=2",
        canonical_url="https://example.com/x",
        content_type="Article",
    )

    post = get_row(db_path=db_path, notion_page_id=page_id)
    assert post is not None
    assert post["url"] == "https://example.com/x?utm=2"
    for col in (
        "raw_content",
        "fetched_at",
        "fetch_tier",
        "fetch_tier_log",
        "fetched_content_char_count",
        "content_hash",
        "extracted_at",
        "extraction_model",
        "extractor_label",
        "extractor_sha256",
        "tokens_in_total",
        "tokens_out_total",
        "title",
        "author",
        "content_date",
        "error_text",
    ):
        assert post[col] is None, f"{col} should be cleared on re-triage, got {post[col]!r}"

    with sqlite3.connect(db_path) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM extraction_calls WHERE notion_page_id=?", (page_id,)
        ).fetchone()[0]
    assert remaining == 0


def test_upsert_triaged_then_fetched_keeps_single_row(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="t-1",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        content_type="article",
    )
    upsert_fetched(
        db_path=db_path,
        notion_page_id="t-1",
        url="https://example.com/new",
        raw_content="x",
        fetch_tier="jina",
        fetch_tier_log=[],
        fetched_content_char_count=1,
        content_hash="h",
    )
    row = get_row(db_path=db_path, notion_page_id="t-1")
    assert row is not None
    # canonical_url + content_type preserved from triage; url updated from fetch.
    assert row["url"] == "https://example.com/new"
    assert row["canonical_url"] == "https://example.com/x"
    assert row["content_type"] == "article"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE notion_page_id=?", ("t-1",)
        ).fetchone()[0]
    assert count == 1


# --- record_extraction_calls tests ---


def test_record_extraction_calls_writes_three_rows_and_cohort_update(db_path: Path):
    _insert_fetched(db_path, page_id="p-1")
    _record_three_call(db_path, page_id="p-1")

    latest = get_latest_extraction_calls(db_path=db_path, notion_page_id="p-1")
    assert set(latest) == {"narrative", "topic_card", "followups"}

    row = get_row(db_path=db_path, notion_page_id="p-1")
    assert row is not None
    assert row["extractor_label"] == "3call_v1"
    assert row["extractor_sha256"] == "b" * 64
    assert row["extraction_model"] == "gpt-4o-mini"
    assert row["tokens_in_total"] == 300
    assert row["tokens_out_total"] == 150
    assert row["extracted_at"] == "2026-06-03T12:00:00+00:00"
    assert row["error_text"] is None


# --- get_queue_extraction tests ---


def test_get_queue_extraction_returns_none_when_row_absent(db_path: Path):
    assert get_queue_extraction(db_path=db_path, notion_page_id="missing") is None


def test_get_queue_extraction_returns_none_when_extracted_at_is_null(db_path: Path):
    _insert_fetched(db_path)
    assert get_queue_extraction(db_path=db_path, notion_page_id="p-1") is None


def test_get_queue_extraction_flattens_topic_card_with_provenance(db_path: Path):
    """Payload keys and provenance keys are both top-level in the returned
    dict — the field shape NA's consumer has historically expected."""
    _insert_fetched(db_path)
    topic_payload = {
        "extracted_title": "Anthropic talk",
        "core_mechanism": "Sleep-time consolidation",
        "best_example": "Replay during SWS",
        "transferable_pattern": "Async memory consolidation",
        "main_tension": "Throughput vs fidelity",
        "candidate_tie_backs": ["agent-memory", "knowledge-os"],
    }
    _record_three_call(db_path, page_id="p-1", topic_payload=topic_payload)

    out = get_queue_extraction(db_path=db_path, notion_page_id="p-1")
    assert out is not None
    # Payload keys flattened to top level.
    assert out["extracted_title"] == "Anthropic talk"
    assert out["candidate_tie_backs"] == ["agent-memory", "knowledge-os"]
    # Provenance keys also top-level.
    assert out["extraction_prompt_label"] == "topic_card_v1"
    assert out["extraction_model"] == "gpt-4o-mini"
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
    _record_three_call(db_path, page_id="recent")
    _record_three_call(db_path, page_id="stale")

    # Stamp both rows directly so the test is independent of the fixture's
    # call timestamps. Stale = 2h ago; recent = now.
    now = datetime.now(UTC)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE queue_items SET extracted_at=? WHERE notion_page_id=?",
            (now.isoformat(), "recent"),
        )
        conn.execute(
            "UPDATE queue_items SET extracted_at=? WHERE notion_page_id=?",
            ((now - timedelta(hours=2)).isoformat(), "stale"),
        )

    stale_rows = list_with_stale_extraction(db_path=db_path, min_age_minutes=60)
    assert {r["notion_page_id"] for r in stale_rows} == {"stale"}


# --- checkpoint_wal tests ---


def test_checkpoint_wal_truncates_wal_sidecar(db_path: Path):
    """After writes, the -wal sidecar grows; checkpoint_wal(TRUNCATE) resets
    it to zero bytes so a reader opening with `immutable=1` (which ignores
    the WAL) still sees committed writes in the main file.

    This is the kp-side companion to the NA-side `immutable=1` fix for the
    "kp_queue_cache returns empty for items in queue" bug observed
    2026-06-08 (see knowledge-os bug 376d130d). Without periodic checkpoint,
    days of writes accumulate in -wal and are invisible to any reader that
    skips WAL sidecars.
    """
    _insert_fetched(db_path, page_id="p-1")
    _record_three_call(db_path, page_id="p-1")

    wal = db_path.with_suffix(db_path.suffix + "-wal")
    assert wal.exists(), "expected -wal sidecar after writes (journal_mode=WAL)"
    assert wal.stat().st_size > 0, "expected -wal to carry the writes pre-checkpoint"

    checkpoint_wal(db_path=db_path)

    assert wal.stat().st_size == 0, "TRUNCATE checkpoint must reset -wal to zero bytes"

    # Reader with immutable=1 (ignores WAL) sees the row → writes landed in
    # the main file.
    uri = f"file:{db_path}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT extractor_label FROM queue_items WHERE notion_page_id=?",
            ("p-1",),
        ).fetchone()
    assert row is not None and row[0] == "3call_v1"


def test_list_with_stale_extraction_returns_extractor_label_not_raw_content(db_path: Path):
    """Lightweight scan — extractor_label (cohort identity for the re-extract
    sensor), not the multi-KB raw_content."""
    _insert_fetched(db_path)
    _record_three_call(db_path, page_id="p-1")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE queue_items SET extracted_at=? WHERE notion_page_id=?",
            ((datetime.now(UTC) - timedelta(hours=2)).isoformat(), "p-1"),
        )
    rows = list_with_stale_extraction(db_path=db_path, min_age_minutes=60)
    assert rows
    row = rows[0]
    assert "raw_content" not in row
    assert row["extractor_label"] == "3call_v1"
    assert row["extractor_sha256"] == "b" * 64


def test_upsert_triaged_stores_user_comments_json(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p1",
        url="u",
        canonical_url="c",
        content_type="Article",
        user_comments_json='[{"text": "focus X"}]',
    )
    row = get_row(db_path=db_path, notion_page_id="p1")
    assert row["user_comments_json"] == '[{"text": "focus X"}]'


def test_retriage_without_comments_wipes_user_comments_json(db_path: Path):
    upsert_triaged(
        db_path=db_path,
        notion_page_id="p1",
        url="u",
        canonical_url="c",
        content_type="Article",
        user_comments_json='[{"text": "focus X"}]',
    )
    upsert_triaged(  # re-triage, no comments this pass
        db_path=db_path,
        notion_page_id="p1",
        url="u",
        canonical_url="c",
        content_type="Article",
        user_comments_json=None,
    )
    row = get_row(db_path=db_path, notion_page_id="p1")
    assert row["user_comments_json"] is None


# --- source_summary (extraction_calls, call_kind="source_summary") ---


def _seed_row(db_path: Path, page_id: str = "p-1") -> None:
    upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url="https://example.com/a",
        canonical_url="https://example.com/a",
        content_type="Article",
    )


def test_get_source_summary_is_none_when_absent(db_path: Path):
    _seed_row(db_path)
    assert get_source_summary(db_path=db_path, notion_page_id="p-1") is None


def test_record_and_get_source_summary_returns_latest_output(db_path: Path):
    _seed_row(db_path)
    record_source_summary(
        db_path=db_path,
        notion_page_id="p-1",
        output="- [reported] First pass.",
        prompt_label="source_summary_system_v1",
        prompt_sha256="a" * 64,
        model="gpt-4.1-mini",
        tokens_in=100,
        tokens_out=50,
    )
    record_source_summary(
        db_path=db_path,
        notion_page_id="p-1",
        output="- [reported] Newer pass.\n- [opinion] A forecast.",
        prompt_label="source_summary_system_v1",
        prompt_sha256="a" * 64,
        model="gpt-4.1-mini",
        tokens_in=120,
        tokens_out=60,
    )
    # Latest-wins, mirroring get_latest_extraction_calls.
    assert (
        get_source_summary(db_path=db_path, notion_page_id="p-1")
        == "- [reported] Newer pass.\n- [opinion] A forecast."
    )


def test_source_summary_coexists_with_topic_card_extraction(db_path: Path):
    # source_summary shares the extraction_calls table with the 3-call extraction
    # but a distinct call_kind — neither read clobbers the other.
    _seed_row(db_path)
    _record_three_call(db_path, page_id="p-1")  # narrative / topic_card / followups
    record_source_summary(
        db_path=db_path,
        notion_page_id="p-1",
        output="- [reported] A claim.",
        prompt_label="source_summary_system_v1",
        prompt_sha256="a" * 64,
        model="gpt-4.1-mini",
        tokens_in=1,
        tokens_out=1,
    )

    assert get_source_summary(db_path=db_path, notion_page_id="p-1") == "- [reported] A claim."
    latest = get_latest_extraction_calls(db_path=db_path, notion_page_id="p-1")
    assert "topic_card" in latest  # voice extraction untouched
    assert latest["source_summary"]["output"] == "- [reported] A claim."
    assert latest["topic_card"]["output"] != "- [reported] A claim."


def test_source_summary_cleared_on_re_triage(db_path: Path):
    _seed_row(db_path)
    record_source_summary(
        db_path=db_path,
        notion_page_id="p-1",
        output="- [reported] X.",
        prompt_label="source_summary_system_v1",
        prompt_sha256="a" * 64,
        model="gpt-4.1-mini",
        tokens_in=1,
        tokens_out=1,
    )
    _seed_row(db_path)  # re-triage same page → cohort reset
    assert get_source_summary(db_path=db_path, notion_page_id="p-1") is None
