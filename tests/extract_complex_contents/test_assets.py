"""Tests for extract_complex_contents assets (3-asset shape).

Materializes individual assets in memory with mock resources and a real
SQLite store (tmp_path). Verifies asset-level invariants: fetched dispatches
by content_type via FetcherResource, extracted persists + check pass/fail,
published flips Notion only when extraction is complete."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from domains.queue_store import sources as queue_db
from orchestrators.defs.extract_complex_contents.assets import (
    extracted,
    fetched,
    published,
)
from orchestrators.defs.extract_complex_contents.def_config import (
    queue_items_partition_def,
)
from orchestrators.defs.extract_complex_contents.extractors import ExtractionUsage
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


def test_fetched_fails_when_below_floor(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_triaged(db_path, "p-1", "YouTube", url="https://youtube.com/watch?v=abc")
    store = QueueStoreResource(db_path=str(db_path))
    fetcher = MagicMock()
    fetcher.fetch_for_type.return_value = FetchResult(content="short", tier="youtube", tier_log=[])
    with pytest.raises(Exception, match="below floor"):
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


def test_extracted_persists_and_passes_check(tmp_path: Path):
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
    row = store.get_row("p-1")
    payload = json.loads(row["extraction_payload"])
    assert payload["extracted_title"] == "JEPA talk"
    assert row["tokens_in"] == 1000
    check_events = [e for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"]
    assert check_events
    assert check_events[0].asset_check_evaluation_data.passed


def test_extracted_check_fails_when_title_missing(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title=None)
    with pytest.raises(Exception):
        _materialize(
            extracted,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )


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


def test_extracted_dispatches_by_content_type(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "arXiv", "a" * 5000)
    store = QueueStoreResource(db_path=str(db_path))
    extractor = _mock_extractor(title="Paper")
    _materialize(
        extracted,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    extractor.extract.assert_called_once()
    assert extractor.extract.call_args.kwargs["content_type"] == "arXiv"


def test_extracted_metadata_includes_extraction_preview(tmp_path: Path):
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
    preview_md = metadata["extraction_preview"].md_str
    assert "Visible Title" in preview_md  # short JSON fits without truncation


# -------- published --------


def test_published_flips_notion_and_writes_core_mechanism_to_description(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    queue_db.update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={
            "extracted_title": "T",
            "core_mechanism": "Distilled mechanism summary.",
        },
        prompt_label="v5_youtube",
        prompt_sha256="a" * 64,
        model="gpt-4o-mini",
        tokens_in=100,
        tokens_out=50,
    )
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


def test_published_skips_description_when_core_mechanism_missing(tmp_path: Path):
    """No core_mechanism in extraction → don't overwrite the Description Notion
    already has (likely the triage-seeded HTML meta)."""
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", "y" * 5000)
    queue_db.update_extracted(
        db_path=db_path,
        notion_page_id="p-1",
        extraction={"extracted_title": "T", "core_mechanism": None},
        prompt_label="v5_youtube",
        prompt_sha256="a" * 64,
        model="gpt-4o-mini",
        tokens_in=100,
        tokens_out=50,
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
