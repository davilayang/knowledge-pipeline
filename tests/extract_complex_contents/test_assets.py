"""Tests for extract_complex_contents assets (3-asset shape).

Materializes individual assets in memory with mock resources and a real
SQLite store (tmp_path). Verifies asset-level invariants: fetched dispatches
by content_type via FetcherResource, extracted persists three extraction_calls
rows + updates queue_items cohort fields, published flips Notion only when
extraction is complete and reads core_mechanism via the latest topic_card row."""

from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard
from domains.queue_store import sources as queue_db
from orchestrators.defs.extract_complex_contents.assets import (
    extracted,
    fetched,
    published,
)
from orchestrators.defs.extract_complex_contents.def_config import (
    queue_items_partition_def,
)
from orchestrators.defs.extract_complex_contents.resources import FetchResult
from orchestrators.defs.shared.queue_resources import QueueStoreResource


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
    db_path: Path, page_id: str, content_type: str, url: str = "https://example.com/x"
) -> None:
    queue_db.create_schema(db_path=db_path)
    queue_db.upsert_triaged(
        db_path=db_path,
        notion_page_id=page_id,
        url=url,
        canonical_url=url,
        content_type=content_type,
    )


def _seed_with_raw_content(
    db_path: Path,
    page_id: str,
    content_type: str,
    raw_content: str,
    url: str = "https://example.com/x",
) -> None:
    _seed_triaged(db_path, page_id, content_type, url)
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
    ex_instance.bundle_label = "3call_v1"
    ex_instance.bundle_sha256 = "b" * 64
    ex_instance.extract.return_value = (payload, calls)

    registry = MagicMock()
    registry.build.return_value = ex_instance
    return registry


# -------- fetched --------


def test_fetched_fails_when_row_missing(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    fetcher = MagicMock()
    with pytest.raises(Exception, match="No queue_items row"):
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
    with pytest.raises(Exception, match="no content_type"):
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
    assert row["extractor_label"] == "3call_v1"
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
    assert metadata["extractor_label"].text == "3call_v1"
    # Both timing perspectives present — total_model_time_ms is sum of per-call
    # durations (what you pay), wall_clock_ms is narrative + max(topic,
    # followups) since calls 2+3 run in parallel inside asyncio.gather.
    assert "total_model_time_ms" in metadata
    assert "wall_clock_ms" in metadata
    assert metadata["wall_clock_ms"].value <= metadata["total_model_time_ms"].value


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


def test_published_flips_notion_and_writes_core_mechanism_to_description(tmp_path: Path):
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
        "p-1", "Ready", description="Distilled mechanism summary."
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
    notion.update_status.assert_called_once_with("p-1", "Ready", description=None)


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
