"""Tests for extract_complex_contents assets.

Materializes individual assets in memory with mock resources and a real
SQLite store (tmp_path). Verifies the asset-level invariants that aren't
captured at the resource layer: re-fetch skip, under-floor failure, topic
card check pass/fail, persist-touches-only-notion."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from orchestrators.defs.extract_complex_contents.assets import (
    fetched_content,
    persisted,
    topic_card,
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


def test_fetched_content_skips_when_raw_content_already_cached(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    store.upsert_fetched(
        notion_page_id="p-1",
        url="https://example.com/x",
        raw_content="cached body",
        fetch_tier="jina",
        fetch_tier_log=[{"tier": "jina"}],
        fetched_content_char_count=11,
        content_hash="h",
    )
    fetcher = MagicMock()
    notion = MagicMock()
    result = _materialize(
        fetched_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "notion": notion, "store": store},
        url="https://example.com/x",
    )
    assert result.success
    fetcher.fetch.assert_not_called()
    notion.update_status.assert_not_called()


def test_fetched_content_fails_when_below_floor(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    fetcher = MagicMock()
    fetcher.fetch.return_value = FetchResult(
        content="short content under floor",
        tier="curl_cffi",
        tier_log=[{"tier": "jina"}, {"tier": "curl_cffi"}],
    )
    notion = MagicMock()
    with pytest.raises(Exception, match="below floor"):
        _materialize(
            fetched_content,
            partition_key="p-1",
            resources={"fetcher": fetcher, "notion": notion, "store": store},
            url="https://example.com/x",
        )
    notion.update_status.assert_called_once_with("p-1", "Fetching")


def test_fetched_content_writes_to_store_on_success(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    fetcher = MagicMock()
    body = "x" * 5000
    fetcher.fetch.return_value = FetchResult(
        content=body, tier="jina", tier_log=[{"tier": "jina", "chars": 5000}]
    )
    notion = MagicMock()
    result = _materialize(
        fetched_content,
        partition_key="p-1",
        resources={"fetcher": fetcher, "notion": notion, "store": store},
        url="https://example.com/x",
    )
    assert result.success
    row = store.get_row("p-1")
    assert row is not None
    assert row["raw_content"] == body
    assert row["fetch_tier"] == "jina"
    assert row["content_hash"]
    notion.update_status.assert_called_once_with("p-1", "Fetching")


def test_topic_card_fails_when_no_raw_content(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    extractor = MagicMock()
    with pytest.raises(Exception, match="No raw_content"):
        _materialize(
            topic_card,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )
    extractor.extract.assert_not_called()


def test_topic_card_writes_extraction_and_passes_check(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    store.upsert_fetched(
        notion_page_id="p-1",
        url="https://example.com/x",
        raw_content="body",
        fetch_tier="jina",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="h",
    )
    extractor = MagicMock()
    extractor.prompt_label = "v5_kp_copy_2026_05_31"
    extractor.prompt_sha256 = "a" * 64
    extractor.model = "anthropic/claude-opus-4-7"
    extractor.extract.return_value = (
        {
            "extracted_title": "T",
            "core_mechanism": "M",
            "best_example": "E",
            "second_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
        ExtractionUsage(input_tokens=1000, output_tokens=200),
    )
    result = _materialize(
        topic_card,
        partition_key="p-1",
        resources={"extractor": extractor, "store": store},
    )
    assert result.success
    row = store.get_row("p-1")
    assert json.loads(row["extraction_payload"])["extracted_title"] == "T"
    assert row["tokens_in"] == 1000
    check_results = [
        evt for evt in result.all_events if evt.event_type_value == "ASSET_CHECK_EVALUATION"
    ]
    assert check_results
    assert check_results[0].asset_check_evaluation_data.passed


def test_topic_card_check_fails_when_extracted_title_missing(tmp_path: Path):
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    store.upsert_fetched(
        notion_page_id="p-1",
        url="https://example.com/x",
        raw_content="body",
        fetch_tier="jina",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="h",
    )
    extractor = MagicMock()
    extractor.prompt_label = "v5"
    extractor.prompt_sha256 = "a"
    extractor.model = "m"
    extractor.extract.return_value = (
        {
            "extracted_title": None,
            "core_mechanism": "M",
            "best_example": "E",
            "second_example": None,
            "transferable_pattern": None,
            "main_tension": None,
            "candidate_tie_backs": [],
        },
        ExtractionUsage(input_tokens=1000, output_tokens=200),
    )
    with pytest.raises(Exception):
        _materialize(
            topic_card,
            partition_key="p-1",
            resources={"extractor": extractor, "store": store},
        )


def test_persisted_only_writes_to_notion(tmp_path: Path):
    notion = MagicMock()
    result = _materialize(persisted, partition_key="p-1", resources={"notion": notion})
    assert result.success
    notion.update_status.assert_called_once_with("p-1", "Ready")
