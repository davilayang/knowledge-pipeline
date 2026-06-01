"""Tests for extract_complex_contents assets (branching topology).

Materializes individual assets in memory with mock resources and a real
SQLite store (tmp_path). Verifies asset-level invariants: router
fails/emits, branch skip gates, re-fetch skip, under-floor failure,
topic card check pass/fail, convergent persist verifies extracted_at."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from domains.raw_store import queue as queue_db
from orchestrators.defs.extract_complex_contents.assets import (
    arxiv_pdf_text,
    arxiv_topic_card,
    persisted,
    routed_for_extraction,
    youtube_topic_card,
    youtube_transcript,
)
from orchestrators.defs.extract_complex_contents.def_config import (
    queue_items_partition_def,
)
from orchestrators.defs.extract_complex_contents.resources import (
    ExtractionUsage,
    ExtractQueueStore,
    FetchResult,
)


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


def _mock_extractor(title: str | None = "Test Title") -> MagicMock:
    extractor = MagicMock()
    extractor.prompt_label.return_value = "v5_youtube_kp_2026_05_31"
    extractor.prompt_sha256.return_value = "a" * 64
    extractor.model = "gpt-4o-mini"
    extractor.extract.return_value = (
        {
            "extracted_title": title,
            "core_mechanism": "mechanism",
            "best_example": "example",
            "second_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
        ExtractionUsage(input_tokens=1000, output_tokens=200),
    )
    return extractor


# -------- routed_for_extraction --------


def test_routed_for_extraction_emits_content_type_metadata(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = ExtractQueueStore(db_path=str(db_path))
    result = _materialize(
        routed_for_extraction,
        partition_key="p-1",
        resources={"store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    assert mat_events
    metadata = mat_events[0].materialization.metadata
    assert metadata["content_type"].text == "YouTube"
    assert metadata["notion_page_id"].text == "p-1"


def test_routed_for_extraction_fails_when_row_missing(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    with pytest.raises(Exception, match="No queue_items row"):
        _materialize(
            routed_for_extraction,
            partition_key="p-missing",
            resources={"store": store},
        )


def test_routed_for_extraction_fails_when_content_type_null(tmp_path: Path):
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
    store = ExtractQueueStore(db_path=str(db_path))
    with pytest.raises(Exception, match="no content_type"):
        _materialize(
            routed_for_extraction,
            partition_key="p-1",
            resources={"store": store},
        )


# -------- youtube_transcript --------


def test_youtube_transcript_skips_when_content_type_is_arxiv(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "arXiv")
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    result = _materialize(
        youtube_transcript,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["skipped"].value is True
    fetcher.fetch_for_type.assert_not_called()


def test_youtube_transcript_skips_when_raw_content_cached(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    result = _materialize(
        youtube_transcript,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["fetch_skipped"].value is True
    fetcher.fetch_for_type.assert_not_called()


def test_youtube_transcript_fetches_and_stores_on_youtube(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    body = "y" * 5000
    fetcher.fetch_for_type.return_value = FetchResult(
        content=body, tier="youtube", tier_log=[{"tier": "youtube"}]
    )
    result = _materialize(
        youtube_transcript,
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


def test_youtube_transcript_fails_when_below_floor(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.fetch_for_type.return_value = FetchResult(content="short", tier="youtube", tier_log=[])
    with pytest.raises(Exception, match="below floor"):
        _materialize(
            youtube_transcript,
            partition_key="p-1",
            resources={"fetcher": fetcher, "store": store},
            url="https://youtube.com/watch?v=abc",
        )


# -------- youtube_topic_card --------


def test_youtube_topic_card_skips_when_content_type_is_arxiv(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "arXiv")
    store = ExtractQueueStore(db_path=str(db_path))
    extractor = MagicMock()
    result = _materialize(
        youtube_topic_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["skipped"].value is True
    check_events = [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]
    assert check_events
    assert check_events[0].asset_check_evaluation_data.metadata["skipped"].value is True
    extractor.extract.assert_not_called()


def test_youtube_topic_card_extracts_and_passes_check_on_youtube(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = ExtractQueueStore(db_path=str(db_path))
    extractor = _mock_extractor(title="JEPA talk")
    result = _materialize(
        youtube_topic_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    row = store.get_row("p-1")
    assert json.loads(row["extraction_payload"])["extracted_title"] == "JEPA talk"
    assert row["tokens_in"] == 1000
    check_events = [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]
    assert check_events
    assert check_events[0].asset_check_evaluation_data.passed


def test_youtube_topic_card_check_fails_when_extracted_title_missing(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = ExtractQueueStore(db_path=str(db_path))
    extractor = _mock_extractor(title=None)
    with pytest.raises(Exception):
        _materialize(
            youtube_topic_card,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )


# -------- arxiv_pdf_text --------


def test_arxiv_pdf_text_skips_when_content_type_is_youtube(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube")
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    result = _materialize(
        arxiv_pdf_text,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["skipped"].value is True
    fetcher.fetch_for_type.assert_not_called()


def test_arxiv_pdf_text_skips_when_raw_content_cached(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "arXiv", "a" * 5000)
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    result = _materialize(
        arxiv_pdf_text,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["fetch_skipped"].value is True
    fetcher.fetch_for_type.assert_not_called()


def test_arxiv_pdf_text_fetches_and_stores_on_arxiv(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "arXiv", url="https://arxiv.org/abs/2401.00001")
    store = ExtractQueueStore(db_path=str(db_path))
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
        arxiv_pdf_text,
        partition_key="p-1",
        resources={"fetcher": fetcher, "store": store},
        url="https://arxiv.org/abs/2401.00001",
    )
    assert result.success
    row = store.get_row("p-1")
    assert row is not None
    assert row["raw_content"] == body
    assert row["fetch_tier"] == "arxiv"
    fetcher.fetch_for_type.assert_called_once_with(
        "https://arxiv.org/abs/2401.00001", content_type="arXiv"
    )
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["arxiv_id"].text == "2401.00001"


def test_arxiv_pdf_text_fails_when_below_floor(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "arXiv", url="https://arxiv.org/abs/2401.00001")
    store = ExtractQueueStore(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.fetch_for_type.return_value = FetchResult(content="tiny", tier="arxiv", tier_log=[])
    with pytest.raises(Exception, match="below floor"):
        _materialize(
            arxiv_pdf_text,
            partition_key="p-1",
            resources={"fetcher": fetcher, "store": store},
            url="https://arxiv.org/abs/2401.00001",
        )


# -------- arxiv_topic_card --------


def test_arxiv_topic_card_skips_when_content_type_is_youtube(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube")
    store = ExtractQueueStore(db_path=str(db_path))
    extractor = MagicMock()
    result = _materialize(
        arxiv_topic_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    metadata = mat_events[0].materialization.metadata
    assert metadata["skipped"].value is True
    check_events = [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]
    assert check_events
    assert check_events[0].asset_check_evaluation_data.metadata["skipped"].value is True
    extractor.extract.assert_not_called()


def test_arxiv_topic_card_extracts_and_passes_check_on_arxiv(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "arXiv", "a" * 5000)
    store = ExtractQueueStore(db_path=str(db_path))
    extractor = _mock_extractor(title="JEPA Paper")
    extractor.prompt_label.return_value = "v5_arxiv_kp_2026_05_31"
    result = _materialize(
        arxiv_topic_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    row = store.get_row("p-1")
    assert json.loads(row["extraction_payload"])["extracted_title"] == "JEPA Paper"
    check_events = [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]
    assert check_events
    assert check_events[0].asset_check_evaluation_data.passed


def test_arxiv_topic_card_check_fails_when_extracted_title_missing(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "arXiv", "a" * 5000)
    store = ExtractQueueStore(db_path=str(db_path))
    extractor = _mock_extractor(title=None)
    with pytest.raises(Exception):
        _materialize(
            arxiv_topic_card,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )


# -------- persisted --------


def test_persisted_flips_notion_to_ready_when_extraction_complete(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    queue_db.update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={"extracted_title": "T", "core_mechanism": "M"},
        prompt_label="v5_youtube",
        prompt_sha256="a" * 64,
        model="gpt-4o-mini",
        tokens_in=100,
        tokens_out=50,
    )
    store = ExtractQueueStore(db_path=str(db_path))
    notion = MagicMock()
    result = _materialize(
        persisted,
        partition_key="p-1",
        resources={"notion": notion, "store": store},
    )
    assert result.success
    notion.update_status.assert_called_once_with("p-1", "Ready")


def test_persisted_fails_when_no_extraction(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube")
    store = ExtractQueueStore(db_path=str(db_path))
    notion = MagicMock()
    with pytest.raises(Exception, match="No extraction completed"):
        _materialize(
            persisted,
            partition_key="p-1",
            resources={"notion": notion, "store": store},
        )
    notion.update_status.assert_not_called()


def test_persisted_fails_when_no_row(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    notion = MagicMock()
    with pytest.raises(Exception, match="No extraction completed"):
        _materialize(
            persisted,
            partition_key="p-missing",
            resources={"notion": notion, "store": store},
        )
    notion.update_status.assert_not_called()
