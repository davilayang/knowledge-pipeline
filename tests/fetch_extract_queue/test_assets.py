"""Tests for fetch_extract_queue assets (3-asset shape).

Materializes individual assets in memory with mock resources and a real
SQLite store (tmp_path). Verifies asset-level invariants: fetched dispatches
by content_type via FetcherResource, extracted persists three extraction_calls
rows + updates queue_items cohort fields, published flips Notion only when
extraction is complete and reads core_mechanism via the latest topic_card row."""

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
    extracted,
    fetched,
    published,
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


# -------- fetched --------


def test_fetched_fails_when_row_missing(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    fetcher = MagicMock()
    with pytest.raises(Exception, match="No local queue_items row"):
        _materialize(
            fetched,
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
            fetched,
            partition_key="p-1",
            resources={"fetcher": fetcher, "store": store},
        )


def test_fetched_skips_when_raw_content_cached(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    result = _materialize(
        fetched,
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
        fetched,
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
        fetched,
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
            fetched,
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
        fetched,
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
        fetched,
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
            fetched,
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
        fetched,
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


# -------- extracted --------


def test_extracted_persists_three_calls_and_passes_check(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="JEPA talk")
    result = _materialize(
        extracted,
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
            extracted,
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
        extracted,
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
        extracted,
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
        extracted,
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
        extracted,
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
        extracted,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    metadata = _materialization_metadata(result)
    assert "Narrative" in metadata["narrative_preview"].md_str
    assert "Visible Title" in metadata["topic_card_preview"].md_str
    assert metadata["followups_count"].value == 4
    assert metadata["extractor_label"].text == "3call_v2_shape_routed"
    # Both timing perspectives present — total_model_time_ms is sum of per-call
    # durations (what you pay), wall_clock_ms is narrative + max(topic,
    # followups) since calls 2+3 run in parallel inside asyncio.gather.
    assert "total_model_time_ms" in metadata
    assert "wall_clock_ms" in metadata
    assert metadata["wall_clock_ms"].value <= metadata["total_model_time_ms"].value


# -------- comments_json_to_user_notes helper --------


def test_comments_json_to_user_notes_formats_bullets():
    raw = '[{"text": "focus on chunking"}, {"text": "compare with dbt"}]'
    assert comments_json_to_user_notes(raw) == "- focus on chunking\n- compare with dbt"


def test_comments_json_to_user_notes_none_when_empty():
    assert comments_json_to_user_notes(None) is None
    assert comments_json_to_user_notes("[]") is None
    assert comments_json_to_user_notes('[{"text": "   "}]') is None


# -------- published --------


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
        published,
        partition_key="p-1",
        resources={"notion": notion, "store": store},
    )
    assert result.success
    notion.update_status.assert_called_once_with(
        "p-1",
        "Ready",
        description="Distilled mechanism summary.",
        name="T",
    )


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
        published,
        partition_key="p-1",
        resources={"notion": notion, "store": store},
    )
    assert result.success
    notion.update_status.assert_called_once_with("p-1", "Ready", description=None, name=None)


def test_published_fails_when_no_extraction(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube")
    store = QueueStoreResource(db_path=str(db_path))
    notion = MagicMock()
    with pytest.raises(Exception, match="No extraction completed"):
        _materialize(
            published,
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
            published,
            partition_key="p-missing",
            resources={"notion": notion, "store": store},
        )
    notion.update_status.assert_not_called()


# -------- extract_claims --------


def test_extract_claims_records_summary_and_passes_content_shape(tmp_path: Path):
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

    def fake_summarize(item, *, content_shape=None):
        captured["content_shape"] = content_shape
        captured["item_id"] = item.item_id
        return summary, LLMCall(content="x", model="gpt-4.1-mini", input_tokens=10, output_tokens=5)

    with patch(
        "orchestrators.defs.fetch_extract_queue.assets.run_extract_claims",
        side_effect=fake_summarize,
    ):
        result = _materialize(extract_claims_asset, partition_key="p-1", resources={"store": store})

    assert result.success
    assert captured["content_shape"] == "podcast_episode"
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
        Candidate(name="Docker", page_type="tool"),
        Candidate(name="Podman", page_type="tool"),
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
