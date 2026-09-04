"""Tests for fetch_extract_queue assets (3-asset shape).

Materializes individual assets in memory with mock resources and a real
SQLite store (tmp_path). Verifies asset-level invariants: fetched dispatches
by content_type via FetcherResource, extracted persists three extraction_calls
rows + updates queue_items cohort fields, published flips Notion only when
extraction is complete and reads core_mechanism via the latest topic_card row."""

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard
from domains.queue_store import sources as queue_db
from domains.types import IngestItem
from orchestrators.defs.fetch_extract_queue.assets import (
    _coerce_author,
    _ingest_item_from_row,
    comments_json_to_user_notes,
    extract_reading_card,
    fetch_content,
    publish_item,
)
from orchestrators.defs.fetch_extract_queue.def_config import (
    queue_items_partition_def,
)
from orchestrators.defs.fetch_extract_queue.resources import FetchResult
from orchestrators.defs.shared.queue_resources import QueueStoreResource


def test_coerce_author_normalizes_to_clean_string_or_none():
    assert _coerce_author(None) is None
    assert _coerce_author([]) is None  # no authors → NULL, not ""
    assert _coerce_author("") is None
    assert _coerce_author(["Vaswani"]) == "Vaswani"
    assert _coerce_author(["A", "B"]) == "A, B"
    assert _coerce_author("Jane Doe") == "Jane Doe"


def test_ingest_item_from_row_maps_queue_row_for_extract_claims():
    row = {
        "notion_page_id": "p-1",
        "url": "https://example.com/a?utm=1",
        "canonical_url": "https://example.com/a",
        "title": "A Title",
        "author": "Jane Doe",
        "content_date": "2026-03-15",
        "raw_content": "body text",
    }
    item = _ingest_item_from_row(row)
    assert isinstance(item, IngestItem)
    assert item.item_id == "https://example.com/a"  # canonical_url — stable key
    assert item.title == "A Title"
    assert item.author == "Jane Doe"
    assert item.date == date(2026, 3, 15)
    assert item.text == "body text"
    assert item.source_ref == "p-1"


def test_ingest_item_from_row_tolerates_missing_metadata():
    row = {
        "notion_page_id": "p-2",
        "url": "https://example.com/b",
        "canonical_url": None,
        "title": None,
        "author": None,
        "content_date": None,
        "raw_content": "body",
    }
    item = _ingest_item_from_row(row)
    assert item.item_id == "https://example.com/b"  # falls back to url
    assert item.title == ""
    assert item.author is None
    assert item.date is None


def _instance_with_partition(page_id: str) -> dg.DagsterInstance:
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(queue_items_partition_def.name, [page_id])
    return instance


def _materialize(asset, *, partition_key: str, resources: dict, url: str | None = None):
    instance = _instance_with_partition(partition_key)
    tags = {"notion_page_id": partition_key}
    if url:
        tags["url"] = url
    return dg.materialize(
        [asset],
        partition_key=partition_key,
        resources=resources,
        instance=instance,
        tags=tags,
    )


def _materialization_metadata(result) -> dict:
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    assert mat_events
    return mat_events[0].materialization.metadata


def _seed_triaged(
    db_path: Path,
    page_id: str,
    content_type: str,
    url: str = "https://example.com/x",
    content_shape: str | None = None,
) -> None:
    queue_db.create_schema(db_path=db_path)
    queue_db.upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url=url,
        canonical_url=url,
        content_type=content_type,
        content_shape=content_shape,
    )


def _seed_with_raw_content(
    db_path: Path,
    page_id: str,
    content_type: str,
    raw_content: str,
    url: str = "https://example.com/x",
    content_shape: str | None = None,
) -> None:
    _seed_triaged(db_path, page_id, content_type, url, content_shape=content_shape)
    queue_db.upsert_fetched(
        db_path=db_path,
        notion_page_id=page_id,
        url=url,
        raw_content=raw_content,
        fetch_tier="youtube",
        fetch_tier_log=[],
        fetched_content_char_count=len(raw_content),
        content_hash="h",
    )


def _make_payload(title: str = "Test Title", followups_n: int = 4) -> ExtractionPayload:
    return ExtractionPayload(
        narrative_md="# Narrative\n\nbody content",
        topic_card=TopicCard(
            extracted_title=title,
            core_mechanism="NAMED-METHOD does VERB to produce OUTCOME.",
            best_example="ORG did SPECIFIC-THING for CONTEXT.",
            second_example=None,
            transferable_pattern="Doing X lets you achieve Y.",
            main_tension="A vs B.",
            candidate_tie_backs=[],
        ),
        followups=Followups(questions=[f"Q{i}?" for i in range(followups_n)]),
    )


def _make_call(call_kind: str, output: str, tokens_in: int = 100, tokens_out: int = 50):
    return ExtractionCallRecord(
        call_kind=call_kind,
        prompt_label=f"{call_kind}_v1",
        prompt_sha256=f"{call_kind}_sha".ljust(64, "0"),
        schema_name=None if call_kind == "narrative" else call_kind.title().replace("_", ""),
        output=output,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_tokens=80,
        duration_ms=500.0,
        extracted_at="2026-06-03T12:00:00+00:00",
    )


def _mock_extractor(title: str = "Test Title", followups_n: int = 4) -> MagicMock:
    """Mock of `ExtractorRegistry` (the resource the asset receives). `.build()`
    returns a mock extractor instance with the actual extract result + the
    three properties the asset reads (bundle_label / bundle_sha256 / model).
    Mirrors the build-once-per-run pattern fixed in this PR's review pass."""
    payload = _make_payload(title=title, followups_n=followups_n)
    calls = [
        _make_call("narrative", payload.narrative_md, 200, 100),
        _make_call("topic_card", payload.topic_card.model_dump_json(), 250, 80),
        _make_call("followups", payload.followups.model_dump_json(), 150, 60),
    ]
    ex_instance = MagicMock()
    ex_instance.model = "gpt-4o-mini"
    ex_instance.bundle_label = "3call_v2_shape_routed"
    # bundle_sha256 is now a method `(content_shape) -> str`. Returning a
    # constant 64-char sha matches what the asset writes to queue_items.
    ex_instance.bundle_sha256 = MagicMock(return_value="b" * 64)
    ex_instance.extract.return_value = (payload, calls)

    registry = MagicMock()
    registry.build.return_value = ex_instance
    return registry


# -------- fetch_content --------


def test_fetched_fails_when_row_missing(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    fetcher = MagicMock()
    with pytest.raises(Exception, match="No local queue_items row"):
        _materialize(
            fetch_content,
            partition_key="p-missing",
            resources={"fetcher": fetcher, "store": store},
        )


def test_fetched_fails_when_content_type_null(tmp_path: Path):
    db_path = tmp_path / "q.db"
    queue_db.create_schema(db_path=db_path)
    queue_db.upsert_fetched(
        db_path=db_path,
        notion_page_id="p-1",
        url="https://example.com/x",
        raw_content="body",
        fetch_tier="jina",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="h",
    )
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    with pytest.raises(Exception, match="no Content Type"):
        _materialize(
            fetch_content,
            partition_key="p-1",
            resources={"fetcher": fetcher, "store": store},
        )


def test_fetched_skips_when_raw_content_cached(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    result = _materialize(
        fetch_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
    )
    assert result.success
    metadata = _materialization_metadata(result)
    assert metadata["fetch_skipped"].value is True
    fetcher.fetch_for_type.assert_not_called()


def test_fetched_dispatches_youtube(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    body = "y" * 5000
    fetcher.fetch_for_type.return_value = FetchResult(
        content=body, tier="youtube", tier_log=[{"tier": "youtube"}]
    )
    result = _materialize(
        fetch_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
        url="https://youtube.com/watch?v=abc",
    )
    assert result.success
    row = store.get_row("p-1")
    assert row is not None
    assert row["raw_content"] == body
    assert row["fetch_tier"] == "youtube"
    fetcher.fetch_for_type.assert_called_once_with(
        "https://youtube.com/watch?v=abc", content_type="YouTube"
    )


def test_fetched_dispatches_arxiv_and_surfaces_extras(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "arXiv", url="https://arxiv.org/abs/2401.00001")
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    body = "a" * 5000
    fetcher.fetch_for_type.return_value = FetchResult(
        content=body,
        tier="arxiv",
        tier_log=[{"tier": "arxiv"}],
        title="Attention Is All You Need",
        extras={"arxiv_id": "2401.00001", "authors": ["Vaswani"], "published": "2024-01-01"},
    )
    result = _materialize(
        fetch_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
        url="https://arxiv.org/abs/2401.00001",
    )
    assert result.success
    fetcher.fetch_for_type.assert_called_once_with(
        "https://arxiv.org/abs/2401.00001", content_type="arXiv"
    )
    metadata = _materialization_metadata(result)
    assert metadata["arxiv_id"].text == "2401.00001"
    assert metadata["title"].text == "Attention Is All You Need"

    # The fetcher metadata is persisted on the queue row (self-sufficient for
    # claim extraction — no raw_store join).
    row = store.get_row("p-1")
    assert row["title"] == "Attention Is All You Need"
    assert row["author"] == "Vaswani"
    assert row["content_date"] == "2024-01-01"


def test_fetched_fails_when_below_extraction_floor(tmp_path: Path):
    """Service's cascade may return sub-floor content (`best_result`
    fallback) when no tier hits its own floor. The asset has its own
    extraction-floor guard so degenerate fetches don't reach the extractor."""
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.fetch_for_type.return_value = FetchResult(
        content="short", tier="transcript_api", tier_log=[]
    )
    with pytest.raises(Exception, match="below extraction floor"):
        _materialize(
            fetch_content,
            partition_key="p-1",
            resources={"fetcher": fetcher, "store": store},
            url="https://youtube.com/watch?v=abc",
        )


def _seed_with_override(
    db_path: Path,
    page_id: str,
    override_text: str,
    *,
    content_type: str = "Article",
    url: str = "https://example.com/x",
) -> None:
    queue_db.create_schema(db_path=db_path)
    queue_db.upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url=url,
        canonical_url=url,
        content_type=content_type,
        raw_content_override=override_text,
    )


def test_fetched_calls_structure_when_override_present(tmp_path: Path):
    db_path = tmp_path / "q.db"
    override = "# Paste\n\n" + ("body text " * 200)
    _seed_with_override(db_path, "p-ovr", override)
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.structure.return_value = FetchResult(
        content="# Clean\n\n" + ("structured body " * 200),
        tier="structurer:gpt-4.1-mini",
        tier_log=[{"tier": "structurer:gpt-4.1-mini"}],
    )

    result = _materialize(
        fetch_content,
        partition_key="p-ovr",
        resources={"fetcher": fetcher, "store": store},
        url="https://example.com/x",
    )

    assert result.success
    fetcher.structure.assert_called_once()
    fetcher.fetch_for_type.assert_not_called()
    call_kwargs = fetcher.structure.call_args
    assert call_kwargs.args[0] == override
    assert call_kwargs.kwargs["source_url"] == "https://example.com/x"

    row = store.get_row("p-ovr")
    assert row is not None
    assert row["fetch_tier"] == "structurer:gpt-4.1-mini"


def test_fetched_falls_through_to_fetch_when_override_empty(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "Article", url="https://example.com/x")
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.fetch_for_type.return_value = FetchResult(
        content="article body " * 500, tier="jina", tier_log=[]
    )

    result = _materialize(
        fetch_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
        url="https://example.com/x",
    )

    assert result.success
    fetcher.fetch_for_type.assert_called_once()
    fetcher.structure.assert_not_called()


def test_fetched_surfaces_structurer_502_as_retryable_failure(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_override(db_path, "p-ovr", "noisy paste " * 500)
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.structure.return_value = FetchResult(
        error="Structurer cascade exhausted: timeout",
        transient=True,
        tier="",
        tier_log=[{"tier": "structurer", "error": "timeout"}],
    )

    # The `fetched` asset carries retry_policy(delay=120); Dagster's in-process
    # executor waits that delay (against a wall-clock deadline) before its one
    # retry. The assertion here is only that a transient structurer failure
    # surfaces as a raised failure — not that Dagster honours the delay — so
    # zero out the computed retry delay.
    with (
        pytest.raises(Exception, match="Structurer cascade exhausted|fetch failed"),
        patch.object(dg.RetryPolicy, "calculate_delay", return_value=0),
    ):
        _materialize(
            fetch_content,
            partition_key="p-ovr",
            resources={"fetcher": fetcher, "store": store},
            url="https://example.com/x",
        )


def test_fetched_metadata_includes_content_preview(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    body = "HEAD" + ("x" * 5000) + "TAIL"
    fetcher.fetch_for_type.return_value = FetchResult(content=body, tier="youtube", tier_log=[])
    result = _materialize(
        fetch_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
        url="https://youtube.com/watch?v=abc",
    )
    assert result.success
    metadata = _materialization_metadata(result)
    preview_md = metadata["content_preview"].md_str
    assert "HEAD" in preview_md
    assert "TAIL" in preview_md
    assert "chars omitted" in preview_md


# -------- extract_reading_card --------


def test_extracted_persists_three_calls_and_passes_check(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="JEPA talk")
    result = _materialize(
        extract_reading_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success

    # Three rows in extraction_calls (one per call_kind), most recent each.
    latest = store.get_latest_extraction_calls("p-1")
    assert set(latest.keys()) == {"narrative", "topic_card", "followups"}
    topic_card_json = latest["topic_card"]["output"]
    assert TopicCard.model_validate_json(topic_card_json).extracted_title == "JEPA talk"

    # queue_items cohort fields updated.
    row = store.get_row("p-1")
    assert row["extractor_label"] == "3call_v2_shape_routed"
    assert row["extractor_sha256"] == "b" * 64
    assert row["tokens_in_total"] == 600  # 200 + 250 + 150
    assert row["tokens_out_total"] == 240  # 100 + 80 + 60
    assert row["extracted_at"] is not None

    # Asset check fires + passes.
    check_events = [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]
    assert check_events
    assert check_events[0].asset_check_evaluation_data.passed


def test_extracted_fails_when_no_raw_content(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube")
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    with pytest.raises(Exception, match="No raw_content"):
        _materialize(
            extract_reading_card,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )


def test_extracted_passes_content_type_to_extractor(tmp_path: Path):
    """content_type flows through to the extractor — each prompt's body branches
    on the [content_type: …] tag the extractor prepends."""
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "arXiv", "a" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="Paper")
    _materialize(
        extract_reading_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    extractor.build.assert_called_once()
    ex_instance = extractor.build.return_value
    ex_instance.extract.assert_called_once()
    assert ex_instance.extract.call_args.kwargs["content_type"] == "arXiv"


def test_extracted_passes_content_shape_from_queue_row(tmp_path: Path):
    """asset reads content_shape from queue_items and passes it
    through to the extractor + bundle_sha256, so the per-shape PromptBundle
    fires."""
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000, content_shape="conference_talk")
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="A talk")
    _materialize(
        extract_reading_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    ex_instance = extractor.build.return_value
    assert ex_instance.extract.call_args.kwargs["content_shape"] == "conference_talk"
    # bundle_sha256 also receives the same shape so the per-row cohort sha
    # reflects the selected bundle, not the unknown fallback.
    ex_instance.bundle_sha256.assert_called_with("conference_talk")


def test_extracted_falls_back_to_unknown_when_content_shape_null(tmp_path: Path):
    """Pre-Phase-3 rows (or rows the classifier flagged as unknown) write
    NULL into queue_items.content_shape; asset must coalesce to the
    extractor's generic fallback so the call still fires."""
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "Article", "a" * 5000, content_shape=None)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="Article")
    _materialize(
        extract_reading_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    ex_instance = extractor.build.return_value
    assert ex_instance.extract.call_args.kwargs["content_shape"] == "unknown"


def test_extracted_builds_extractor_exactly_once_per_run(tmp_path: Path):
    """Each call to ExtractorRegistry.build() constructs a fresh AsyncOpenAI
    client. The asset must call build() ONCE per materialization and reuse
    the returned instance — calling build() more than once leaks httpx pools.
    Locks in the fix surfaced by the codex review on PR #79."""
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="JEPA")
    _materialize(
        extract_reading_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert extractor.build.call_count == 1


def test_extracted_metadata_includes_narrative_and_topic_card_previews(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="Visible Title")
    result = _materialize(
        extract_reading_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    metadata = _materialization_metadata(result)
    assert "Narrative" in metadata["narrative_preview"].md_str
    assert "Visible Title" in metadata["topic_card_preview"].md_str
    assert metadata["followups_count"].value == 4
    assert metadata["extractor_label"].text == "3call_v2_shape_routed"
    # One timing number: the three calls run one after another, so model time
    # and wall clock are the same figure and reporting both invited the reader
    # to believe the pair still overlapped.
    assert "total_model_time_ms" in metadata
    assert "wall_clock_ms" not in metadata


# -------- comments_json_to_user_notes helper --------


def test_comments_json_to_user_notes_formats_bullets():
    raw = '[{"text": "focus on chunking"}, {"text": "compare with dbt"}]'
    assert comments_json_to_user_notes(raw) == "- focus on chunking\n- compare with dbt"


def test_comments_json_to_user_notes_none_when_empty():
    assert comments_json_to_user_notes(None) is None
    assert comments_json_to_user_notes("[]") is None
    assert comments_json_to_user_notes('[{"text": "   "}]') is None


# -------- publish_item --------


def _record_three_call_extraction(
    db_path: Path,
    page_id: str,
    *,
    core_mechanism: str = "Distilled mechanism summary.",
):
    topic_card = TopicCard(
        extracted_title="T",
        core_mechanism=core_mechanism,
        best_example="Example.",
        transferable_pattern="Pattern.",
        main_tension="Tension.",
    )
    followups = Followups(questions=["a?", "b?", "c?", "d?"])
    queue_db.record_extraction_calls(
        db_path=db_path,
        notion_page_id=page_id,
        extractor_label="3call_v1",
        extractor_sha256="b" * 64,
        model="gpt-4o-mini",
        calls=[
            _make_call("narrative", "# narrative"),
            _make_call("topic_card", topic_card.model_dump_json()),
            _make_call("followups", followups.model_dump_json()),
        ],
        tokens_in_total=600,
        tokens_out_total=240,
    )


def test_published_flips_notion_and_writes_topic_card_to_name_and_description(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    _record_three_call_extraction(db_path, "p-1", core_mechanism="Distilled mechanism summary.")
    store = QueueStoreResource(db_path=str(db_path))
    notion = MagicMock()
    result = _materialize(
        publish_item,
        partition_key="p-1",
        resources={"notion": notion, "store": store},
    )
    assert result.success
    notion.update_status.assert_called_once_with(
        "p-1",
        "Ready",
        description="Distilled mechanism summary.",
        name="T",
        published_date=None,
    )


def test_published_writes_content_date_back_to_notion(tmp_path: Path):
    # A content_date on the row (user-set or fetcher-discovered) is written back to
    # Notion's Publish Date in the same status flip, so the date surfaces there.
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    queue_db.upsert_fetched(
        db_path=db_path,
        notion_page_id="p-1",
        url="https://example.com/x",
        raw_content="y" * 5000,
        fetch_tier="youtube",
        fetch_tier_log=[],
        fetched_content_char_count=5000,
        content_hash="h",
        content_date="2026-03-01",
    )
    _record_three_call_extraction(db_path, "p-1")
    store = QueueStoreResource(db_path=str(db_path))
    notion = MagicMock()
    result = _materialize(
        publish_item, partition_key="p-1", resources={"notion": notion, "store": store}
    )
    assert result.success
    assert notion.update_status.call_args.kwargs["published_date"] == "2026-03-01"


def test_published_skips_description_when_no_topic_card_row(tmp_path: Path):
    """No topic_card row in extraction_calls → don't overwrite the Description
    Notion already has (likely the triage-seeded HTML meta). This shouldn't
    happen under the three-call shape (all-or-nothing) but the published asset
    must tolerate it defensively for forwards-compat with future extractors
    that emit different call_kinds."""
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    # Write a cohort row but no extraction_calls rows — simulates an extractor
    # that has not yet produced a topic_card (unreachable today; defensive).
    queue_db.record_extraction_calls(
        db_path=db_path,
        notion_page_id="p-1",
        extractor_label="future_v1",
        extractor_sha256="c" * 64,
        model="gpt-4o-mini",
        calls=[_make_call("narrative", "# narrative")],
        tokens_in_total=100,
        tokens_out_total=50,
    )
    store = QueueStoreResource(db_path=str(db_path))
    notion = MagicMock()
    result = _materialize(
        publish_item,
        partition_key="p-1",
        resources={"notion": notion, "store": store},
    )
    assert result.success
    notion.update_status.assert_called_once_with(
        "p-1", "Ready", description=None, name=None, published_date=None
    )


def test_published_fails_when_no_extraction(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube")
    store = QueueStoreResource(db_path=str(db_path))
    notion = MagicMock()
    with pytest.raises(Exception, match="No extraction completed"):
        _materialize(
            publish_item,
            partition_key="p-1",
            resources={"notion": notion, "store": store},
        )
    notion.update_status.assert_not_called()


def test_published_fails_when_no_row(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    notion = MagicMock()
    with pytest.raises(Exception, match="No extraction completed"):
        _materialize(
            publish_item,
            partition_key="p-missing",
            resources={"notion": notion, "store": store},
        )
    notion.update_status.assert_not_called()


# -------- extract_claims --------


def test_extract_claims_records_summary_and_passes_lowercased_content_type(tmp_path: Path):
    from domains.wiki.claims import (
        ClaimSet,
        SourceClaim,
        render_claims,
    )
    from orchestrators.defs.fetch_extract_queue.assets import extract_claims as extract_claims_asset
    from workflows.llm import LLMCall

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(
        db_path, "p-1", "YouTube", "body about Claude Code", content_shape="podcast_episode"
    )
    store = QueueStoreResource(db_path=str(db_path))

    summary = ClaimSet(
        item_id="https://example.com/x",
        content_date=None,
        claims=[
            SourceClaim(text="A forecast.", source_id="https://example.com/x", speculative=True),
        ],
    )
    captured = {}

    def fake_summarize(item, *, content_type=None):
        captured["content_type"] = content_type
        captured["item_id"] = item.item_id
        return summary, LLMCall(content="x", model="gpt-4.1-mini", input_tokens=10, output_tokens=5)

    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_claims",
        side_effect=fake_summarize,
    ):
        result = _materialize(extract_claims_asset, partition_key="p-1", resources={"store": store})

    assert result.success
    # Seeded as "YouTube"; lower-cased on the way through.
    assert captured["content_type"] == "youtube"
    assert captured["item_id"] == "https://example.com/x"
    assert store.get_claims("p-1") == render_claims(summary)


def test_extract_claims_skips_when_no_body(tmp_path: Path):
    from orchestrators.defs.fetch_extract_queue.assets import extract_claims as extract_claims_asset

    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "Article")  # triaged but never fetched → no raw_content
    store = QueueStoreResource(db_path=str(db_path))
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_claims"
    ) as mock_summarize:
        result = _materialize(extract_claims_asset, partition_key="p-1", resources={"store": store})
    assert result.success
    mock_summarize.assert_not_called()
    assert store.get_claims("p-1") is None


# -------- extract_entities --------


def _seed_body_and_claims(db_path: Path, page_id: str) -> None:
    """Seed a fetched body AND a recorded extract_claims doc — the two inputs the
    extract_entities asset consumes (raw_content + the stored claims)."""
    from domains.wiki.claims import ClaimSet, SourceClaim, render_claims

    _seed_with_raw_content(db_path, page_id, "Article", "body naming Docker and Podman")
    store = QueueStoreResource(db_path=str(db_path))
    claim_set = ClaimSet(
        item_id="https://example.com/x",
        content_date=None,
        claims=[
            SourceClaim(text="Docker was dropped for Podman.", source_id="https://example.com/x")
        ],
    )
    store.record_claims(
        notion_page_id=page_id,
        output=render_claims(claim_set),
        prompt_label="extract_claims_v1",
        prompt_sha256="sha",
        model="gpt-4.1-mini",
        tokens_in=1,
        tokens_out=1,
    )


def test_extract_entities_records_candidates_with_cached_tokens(tmp_path: Path):
    from domains.wiki.identity import Candidate
    from orchestrators.defs.fetch_extract_queue.assets import (
        extract_entities as extract_entities_asset,
    )
    from workflows.llm import LLMCall
    from workflows.wiki_synthesis.extract_entities import render_candidates

    db_path = tmp_path / "q.db"
    _seed_body_and_claims(db_path, "p-1")
    store = QueueStoreResource(db_path=str(db_path))

    candidates = [
        Candidate(name="Docker", entity_type="tool"),
        Candidate(name="Podman", entity_type="tool"),
    ]
    captured = {}

    def fake_entities(item, claims):
        captured["item_id"] = item.item_id
        captured["n_claims"] = len(claims.claims)
        return candidates, LLMCall(
            content="x", model="gpt-4.1-mini", input_tokens=200, output_tokens=8, cached_tokens=160
        )

    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_entities",
        side_effect=fake_entities,
    ):
        result = _materialize(
            extract_entities_asset, partition_key="p-1", resources={"store": store}
        )

    assert result.success
    # The stored claims were parsed and handed to the extractor with the article item.
    assert captured["item_id"] == "https://example.com/x"
    assert captured["n_claims"] == 1
    # Candidates persisted in canonical form; cache-hit recorded.
    assert store.get_candidates("p-1") == render_candidates(candidates)
    assert store.get_latest_extraction_calls("p-1")["extract_entities"]["cached_tokens"] == 160


def test_extract_entities_skips_when_no_body(tmp_path: Path):
    from orchestrators.defs.fetch_extract_queue.assets import (
        extract_entities as extract_entities_asset,
    )

    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "Article")  # no raw_content
    store = QueueStoreResource(db_path=str(db_path))
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_entities"
    ) as mock_entities:
        result = _materialize(
            extract_entities_asset, partition_key="p-1", resources={"store": store}
        )
    assert result.success
    mock_entities.assert_not_called()
    assert store.get_candidates("p-1") is None


def test_extract_entities_skips_when_no_claims(tmp_path: Path):
    # Body fetched but extract_claims recorded nothing (its dep produced no claims)
    # → no article-companion to ground against; skip rather than run claims-less.
    from orchestrators.defs.fetch_extract_queue.assets import (
        extract_entities as extract_entities_asset,
    )

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "Article", "body with no recorded claims")
    store = QueueStoreResource(db_path=str(db_path))
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_entities"
    ) as mock_entities:
        result = _materialize(
            extract_entities_asset, partition_key="p-1", resources={"store": store}
        )
    assert result.success
    mock_entities.assert_not_called()
    assert store.get_candidates("p-1") is None


# -------- extract_metadata --------


def _check_events(result):
    return [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("connection reset by peer"),
        ValueError("reply contains fields the schema does not declare: ['shape']"),
    ],
    ids=["transient_network", "unusable_reply"],
)
def test_extract_metadata_writes_nothing_but_still_materializes_on_failure(tmp_path: Path, exc):
    """A failure here would gate the reading card AND the claims lane — a blast
    radius neither has today. A missing metadata row costs nothing; a blocked
    extraction costs the item. It materialises; the check turns red."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        side_effect=exc,
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    row = store.get_row("p-1")
    assert row["contributors_json"] is None
    assert row["publisher"] is None
    assert "metadata" not in store.get_latest_extraction_calls("p-1")

    checks = _check_events(result)
    assert checks and not checks[0].asset_check_evaluation_data.passed


def _metadata_payload(**overrides):
    from workflows.extraction.metadata import Contributor, MetadataPayload

    fields = dict(
        contributors=[
            Contributor(name="Hugo Lu", role="author", affiliation=None),
            Contributor(name="Kyle Cheung", role="author", affiliation="Greybeam"),
        ],
        publisher="Orchestra",
        # The common case, so a test that is not about the gate need not say so.
        stands_alone=True,
    )
    fields.update(overrides)
    return MetadataPayload(**fields)


def _metadata_call(payload):
    from workflows.llm import LLMCall

    return LLMCall(
        content=payload.model_dump_json(),
        model="gpt-5-mini",
        input_tokens=900,
        output_tokens=120,
        cached_tokens=800,
        finish_reason="stop",
    )


def test_extract_metadata_persists_columns_and_a_call_row(tmp_path: Path):
    """The columns are the artefact, the call row its provenance; both come from
    one payload, so a row cannot carry metadata without the call that made it.
    Contributors stay multi-valued and independent of publisher — one guest post
    has a platform byline, a real author, and a newsletter that is neither."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload()
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    row = store.get_row("p-1")
    assert [c["name"] for c in json.loads(row["contributors_json"])] == ["Hugo Lu", "Kyle Cheung"]
    assert json.loads(row["contributors_json"])[1]["affiliation"] == "Greybeam"
    assert row["publisher"] == "Orchestra"
    # `author` is the source platform's raw byline and keeps that meaning — no
    # existing row changes meaning when this asset lands.
    assert row["author"] is None

    call = store.get_latest_extraction_calls("p-1")["metadata"]
    assert call["prompt_label"] == "metadata_v1"
    assert call["model"] == "gpt-5-mini"
    assert json.loads(call["node_metadata"])["content_hash"] == row["content_hash"]

    checks = _check_events(result)
    assert checks and checks[0].asset_check_evaluation_data.passed


def test_extract_metadata_prefers_the_youtube_channel_over_the_models_publisher(tmp_path: Path):
    """oEmbed's author_name IS the channel and the channel IS the publisher, so it
    wins. The model's answer survives in the call ledger rather than being dropped
    — a repeated disagreement is how we would learn the deterministic source is
    wrong for a lane."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "youtube", "transcript " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    store.upsert_enriched(
        notion_page_id="p-1",
        url="https://example.com/x",
        enrichment_json=json.dumps({"youtube": {"channel": "AI Engineer", "title": "A talk"}}),
    )
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(publisher="Together AI")
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    row = store.get_row("p-1")
    assert row["publisher"] == "AI Engineer"
    call = store.get_latest_extraction_calls("p-1")["metadata"]
    assert json.loads(call["output"])["publisher"] == "Together AI"


@pytest.mark.parametrize(
    ("content_type", "sitename", "expected_publisher"),
    [
        ("article", "Substack", "Together AI"),
        ("medium", "Medium", "Together AI"),
        ("facebook", "Facebook", "Together AI"),
    ],
    ids=["article_catch_all_defers", "medium_platform_ignored", "facebook_platform_ignored"],
)
def test_extract_metadata_never_takes_a_site_name_as_the_publisher(
    tmp_path: Path, content_type: str, sitename: str, expected_publisher: str
):
    """`article` is the fetcher's CATCH-ALL, not "a plain web article", so its
    `og:site_name` is as likely to be Substack, LinkedIn or Reddit as a real
    publication. Trusting it would bury the publication that ran the piece. The
    model, which reads the body, decides instead."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", content_type, "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    store.upsert_enriched(
        notion_page_id="p-1",
        url="https://example.com/x",
        enrichment_json=json.dumps({"article": {"sitename": sitename}}),
    )
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(publisher="Together AI")
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    assert store.get_row("p-1")["publisher"] == expected_publisher


def _materialize_metadata_twice(tmp_path: Path, *, refetch_body: str | None = None):
    """Run extract_metadata, optionally re-fetch a different body, run it again.
    Returns (mock, store) so callers can count the calls the second run made."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload()
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ) as call:
        _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )
        if refetch_body is not None:
            queue_db.upsert_fetched(
                db_path=db_path,
                notion_page_id="p-1",
                url="https://example.com/x",
                raw_content=refetch_body,
                fetch_tier="jina",
                fetch_tier_log=[],
                fetched_content_char_count=len(refetch_body),
                content_hash="a-different-hash",
            )
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )
    assert result.success
    return call, store


def test_extract_metadata_skips_the_call_when_body_and_prompt_are_unchanged(tmp_path: Path):
    """Re-materialising an unchanged row must cost nothing. The columns being
    populated is not sufficient on its own — the check is what body and which
    prompt produced them, so a re-run after a backfill or a partition sweep does
    not re-buy the same answer."""
    call, store = _materialize_metadata_twice(tmp_path)
    assert call.call_count == 1
    assert store.get_row("p-1")["contributors_json"] is not None


def test_extract_metadata_re_extracts_when_the_body_changed(tmp_path: Path):
    """A re-fetch replaces the body but leaves these columns in place, so
    populated-columns alone would serve metadata read off content that no longer
    exists — the stale-body failure the fetch cache hit in PR #109."""
    call, _store = _materialize_metadata_twice(tmp_path, refetch_body="an entirely new body " * 50)
    assert call.call_count == 2


def test_extract_metadata_still_materializes_when_the_store_write_fails(tmp_path: Path):
    """The contract has to cover the whole asset, not just the model call. queue.db
    is written by triage and read whole-corpus by the wiki sweep, so a lock during
    the write is realistic — and it would stop both extract branches."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload()
    with (
        patch(
            "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
            return_value=(payload, _metadata_call(payload)),
        ),
        patch.object(
            QueueStoreResource, "record_metadata", side_effect=RuntimeError("database is locked")
        ),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    checks = _check_events(result)
    assert checks and not checks[0].asset_check_evaluation_data.passed


def test_extract_metadata_migrates_the_schema_it_writes_to(tmp_path: Path):
    """This asset can be materialised alone — a backfill over stored bodies is
    exactly that — so it cannot rely on a sibling having migrated first. Only
    `fetch_content` calls ensure_schema; without one here the write hits
    `no such column: contributors_json`."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    # Roll the file back to the shape it had before this asset existed.
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        for col in ("contributors_json", "publisher"):
            conn.execute(f"ALTER TABLE queue_items DROP COLUMN {col}")
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload()
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    assert store.get_row("p-1")["contributors_json"] is not None


def test_extract_metadata_re_extracts_when_the_model_changed(tmp_path: Path):
    """Two models' answers in one column is the corpus-labelling problem the prompt
    hash exists to prevent. The three-call lane already folds the model into its
    staleness signal; without it here, a model swap freezes every existing row."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    payload = _metadata_payload()
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ) as call:
        first = MagicMock()
        first.model = "gpt-4.1-mini"
        _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": first, "store": store},
        )
        swapped = MagicMock()
        swapped.model = "gpt-5-mini"
        _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": swapped, "store": store},
        )
    assert call.call_count == 2


def test_extract_metadata_uses_the_repo_owner_as_the_github_publisher(tmp_path: Path):
    """A GitHub repo's owner is in the URL path and is unambiguous, which is the
    whole test for letting a deterministic source win. The og:site_name for the
    same page is "GitHub", which is why the HTML metadata is not used here."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(
        db_path, "p-1", "github", "readme " * 200, url="https://github.com/langchain-ai/langgraph"
    )
    store = QueueStoreResource(db_path=str(db_path))
    store.upsert_enriched(
        notion_page_id="p-1",
        url="https://github.com/langchain-ai/langgraph",
        # The same page's og:site_name reads "GitHub" — the platform, not the
        # publisher — which is why the URL is the source here and HTML metadata
        # is never trusted for this field.
        enrichment_json=json.dumps({"article": {"sitename": "GitHub"}}),
    )
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(publisher="LangGraph")
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    assert store.get_row("p-1")["publisher"] == "langchain-ai"


def test_extract_metadata_records_call_latency(tmp_path: Path):
    """Per-call latency for this lane is only answerable from the ledger, and
    Phase C reads it to judge what the extra call costs. Without it every metadata
    row carries NULL where the three-call rows carry a number."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "article", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload()
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    call_row = store.get_latest_extraction_calls("p-1")["metadata"]
    assert call_row["duration_ms"] is not None
    assert call_row["duration_ms"] >= 0


def test_extract_metadata_fails_the_row_when_the_fetch_arrived_damaged(tmp_path: Path):
    """A body whose substance never arrived is worse than no body, because
    nothing downstream can tell the difference — the claims lane will happily
    extract from navigation chrome. So the item stops, and the run-failure sensor
    turns that into Status=Failed in Notion. The columns are written first: the
    failed row still shows what was missing."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata
    from workflows.extraction.metadata import Unreadable

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "github", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(
        stands_alone=False,
        stands_alone_reason="the article is replaced by an error page; no README text is present",
        unreadable=[
            Unreadable(
                cause="chrome",
                missing="the repository README",
                evidence="There was an error while loading. Please reload this page",
            )
        ],
    )
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        with pytest.raises(dg.Failure) as exc:
            _materialize(
                extract_metadata,
                partition_key="p-1",
                resources={"extractor": extractor, "store": store},
            )

    # The Notion row gets this text verbatim, so it has to name the specific
    # missing thing — "extraction failed" tells the reader nothing to act on.
    assert "the repository README" in exc.value.description

    row = store.get_row("p-1")
    assert json.loads(row["unreadable_json"])[0]["cause"] == "chrome"
    assert "metadata" in store.get_latest_extraction_calls("p-1")


def test_extract_metadata_records_visual_dependence_without_failing(tmp_path: Path):
    """Half of all conference talks point at a slide, and a paper referencing
    Figure 4 is the normal shape of a paper — not a defect. Measured over the
    227-body production corpus, failing on those would fail 41% of ingests.
    They are recorded so a reader knows the piece leans on visuals."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata
    from workflows.extraction.metadata import Unreadable

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "youtube", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(
        stands_alone=True,
        unreadable=[
            Unreadable(
                cause="screen_reference",
                missing="the benchmark chart he reads numbers off",
                evidence="as you can see here, the numbers are dramatically better",
            )
        ],
    )
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    row = store.get_row("p-1")
    assert json.loads(row["unreadable_json"])[0]["cause"] == "screen_reference"


def test_extract_metadata_keeps_failing_a_damaged_row_it_does_not_recall(tmp_path: Path):
    """Re-materialising an unchanged body skips the call to save the tokens, and
    a gate that only fires on a fresh call would quietly stop applying on the
    second run — the reading card would proceed on the same navigation chrome
    that failed a minute earlier. The verdict is re-read from the stored row."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata
    from workflows.extraction.metadata import Unreadable

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "github", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(
        stands_alone=False,
        stands_alone_reason="the article is replaced by an error page; no README text is present",
        unreadable=[
            Unreadable(
                cause="chrome",
                missing="the repository README",
                evidence="There was an error while loading. Please reload this page",
            )
        ],
    )
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ) as call:
        for _ in range(2):
            with pytest.raises(dg.Failure) as exc:
                _materialize(
                    extract_metadata,
                    partition_key="p-1",
                    resources={"extractor": extractor, "store": store},
                )

    assert "the repository README" in exc.value.description
    # The second run re-read the verdict rather than paying for it again.
    assert call.call_count == 1


def test_extract_metadata_still_fails_a_damaged_row_when_the_wal_checkpoint_trips(tmp_path: Path):
    """The columns are already committed by the time the WAL is checkpointed, so
    a busy-database hiccup there says nothing about whether the body was
    readable. Routing it through the catch-all handler would return past the
    gate and release the reading card onto navigation chrome — the one thing
    this asset exists to stop."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata
    from workflows.extraction.metadata import Unreadable

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "github", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(
        stands_alone=False,
        stands_alone_reason="the article is replaced by an error page; no README text is present",
        unreadable=[
            Unreadable(
                cause="chrome",
                missing="the repository README",
                evidence="There was an error while loading. Please reload this page",
            )
        ],
    )
    with (
        patch(
            "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
            return_value=(payload, _metadata_call(payload)),
        ),
        patch.object(
            QueueStoreResource,
            "checkpoint_wal",
            side_effect=sqlite3.OperationalError("database is locked"),
        ),
    ):
        with pytest.raises(dg.Failure) as exc:
            _materialize(
                extract_metadata,
                partition_key="p-1",
                resources={"extractor": extractor, "store": store},
            )

    assert "the repository README" in exc.value.description


def test_extract_metadata_fails_a_body_that_does_not_stand_alone(tmp_path: Path):
    """The gate asks whether the text carries enough of the piece to stand on
    its own, and nothing else. It used to ask whether a refetch would recover
    the missing material, which excused the rows that most need a human: a talk
    whose substance stayed on the speaker's screen is both unusable and
    unfixable by refetching.

    So the cause plays no part. `screen_reference` is the one no refetch
    repairs, and here it fails, because the piece does not survive without it."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata
    from workflows.extraction.metadata import Unreadable

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "youtube", "body " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(
        stands_alone=False,
        stands_alone_reason="every benchmark score is read off a chart and never spoken",
        unreadable=[
            Unreadable(
                cause="screen_reference",
                missing="the benchmark scores",
                evidence="as you can see here, it is inching up there",
            )
        ],
    )
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        with pytest.raises(dg.Failure) as exc:
            _materialize(
                extract_metadata,
                partition_key="p-1",
                resources={"extractor": extractor, "store": store},
            )

    assert "every benchmark score is read off a chart" in exc.value.description


def test_the_action_names_the_cheapest_repair_that_could_work():
    """The curator reads one action, so a row whose gaps are mixed has to name
    the one worth trying first. Refetching is cheap and sometimes works;
    hunting down another source is neither, so it is the fallback rather than
    the headline. Derived from the causes rather than asked of the model, which
    has no idea what this repo can refetch."""
    from orchestrators.defs.fetch_extract_queue.assets import _action_for

    assert _action_for(["chrome"]) == "REFETCH"
    assert _action_for(["truncation"]) == "REFETCH"
    assert _action_for(["screen_reference"]) == "FIND_ANOTHER_SOURCE"
    assert _action_for(["images"]) == "FIND_ANOTHER_SOURCE"
    # Substance that is present but unspeakable has no repair to offer.
    assert _action_for(["unspeakable"]) == "NONE"
    assert _action_for([]) == "NONE"
    # Mixed: a refetch might fix half of it, so it is what to try first.
    assert _action_for(["screen_reference", "truncation"]) == "REFETCH"


def test_the_unusable_record_carries_the_reason_not_only_the_absence():
    """The Notion Error field gets this verbatim. Listing what is missing
    without saying why the piece does not hold leaves the reader to re-derive
    the verdict — every talk is missing something, so absence alone does not
    explain a failure. Structured because it is a log entry, scanned and
    grepped, not a letter."""
    from orchestrators.defs.fetch_extract_queue.assets import _unusable_record

    record = _unusable_record(
        {
            "stands_alone": False,
            "stands_alone_reason": "the benchmark scores are read off a chart and never spoken",
            "unreadable": [
                {
                    "cause": "screen_reference",
                    "missing": "the benchmark comparison at ~12:30",
                    "evidence": "as you can see here, the gap is substantial",
                }
            ],
        }
    )

    assert "verdict:  UNUSABLE" in record
    assert "reason:   the benchmark scores are read off a chart and never spoken" in record
    assert "action:   FIND_ANOTHER_SOURCE" in record
    assert "causes:   screen_reference=1" in record
    assert "  - screen_reference | the benchmark comparison at ~12:30" in record
    assert 'evidence | "as you can see here, the gap is substantial"' in record


def test_the_unusable_record_says_so_when_the_model_gave_no_reason():
    """`stands_alone_reason` is required by the prompt but not by the schema —
    a default of "" keeps a missing reason from throwing away a verdict that is
    otherwise actionable. The record has to show the gap rather than print a
    blank line the reader mistakes for a rendering fault."""
    from orchestrators.defs.fetch_extract_queue.assets import _unusable_record

    record = _unusable_record({"stands_alone": False, "unreadable": []})

    assert "reason:   (the model gave none)" in record
    assert "causes:   (none reported)" in record


def test_a_channel_named_after_its_presenter_does_not_become_a_second_entity(tmp_path: Path):
    """The deterministic channel wins over the model — except when the channel IS
    a contributor. Writing that human into `publisher` too mints two wiki entities
    for one person, one filed as an organisation, which is the class confusion the
    downstream wiki has no way to undo."""
    from orchestrators.defs.fetch_extract_queue.assets import extract_metadata
    from workflows.extraction.metadata import Contributor

    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "youtube", "transcript " * 200)
    store = QueueStoreResource(db_path=str(db_path))
    store.upsert_enriched(
        notion_page_id="p-1",
        url="https://example.com/x",
        enrichment_json=json.dumps({"youtube": {"channel": "Evan Edinger", "title": "A video"}}),
    )
    extractor = MagicMock()
    extractor.model = "gpt-5-mini"
    payload = _metadata_payload(
        contributors=[Contributor(name="Evan Edinger", role="presenter", affiliation=None)],
        publisher=None,
    )
    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_metadata",
        return_value=(payload, _metadata_call(payload)),
    ):
        result = _materialize(
            extract_metadata,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )

    assert result.success
    assert store.get_row("p-1")["publisher"] is None
