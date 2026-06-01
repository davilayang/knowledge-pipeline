"""Tests for triage_queued_items assets."""

from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.triage_queued_items.assets import classified, routed
from orchestrators.defs.triage_queued_items.def_config import queue_items_partition_def
from orchestrators.defs.triage_queued_items.resources import TriageQueueStore


def _instance_with_partition(page_id: str) -> dg.DagsterInstance:
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(queue_items_partition_def.name, [page_id])
    return instance


def _materialize(
    asset,
    *,
    partition_key: str,
    resources: dict,
    url: str,
    notion_name: str = "",
):
    instance = _instance_with_partition(partition_key)
    op_name = "__".join(asset.key.path)
    return dg.materialize(
        [asset],
        partition_key=partition_key,
        resources=resources,
        instance=instance,
        tags={"notion_page_id": partition_key},
        run_config={
            "ops": {op_name: {"config": {"url": url, "notion_name": notion_name}}},
        },
    )


def _get_metadata(result) -> dict:
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    assert mat_events
    return mat_events[0].materialization.metadata


# -------- classified --------


def test_classified_returns_youtube_for_youtube_url():
    title_fetcher = MagicMock()
    title_fetcher.fetch_title.return_value = None
    result = _materialize(
        classified,
        partition_key="p-1",
        resources={"title_fetcher": title_fetcher},
        url="https://youtube.com/watch?v=xx12345abcd",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"
    assert metadata["tier"].text == "A"


def test_classified_uses_notion_name_when_provided():
    title_fetcher = MagicMock()
    result = _materialize(
        classified,
        partition_key="p-1",
        resources={"title_fetcher": title_fetcher},
        url="https://example.com/post",
        notion_name="Hello",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["name_source"].text == "notion"
    assert metadata["name"].text == "Hello"
    title_fetcher.fetch_title.assert_not_called()


def test_classified_falls_back_to_title_fetch_when_notion_name_empty():
    title_fetcher = MagicMock()
    title_fetcher.fetch_title.return_value = "Page Title"
    result = _materialize(
        classified,
        partition_key="p-1",
        resources={"title_fetcher": title_fetcher},
        url="https://example.com/post",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["name_source"].text == "fetched"
    assert metadata["name"].text == "Page Title"


# -------- routed --------


def test_routed_writes_status_fetching_for_tier_a(tmp_path: Path):
    db_path = tmp_path / "q.db"
    store = TriageQueueStore(db_path=str(db_path))
    notion = MagicMock()
    title_fetcher = MagicMock()
    title_fetcher.fetch_title.return_value = None
    result = _materialize(
        routed,
        partition_key="p-1",
        resources={"triage_notion": notion, "triage_store": store, "title_fetcher": title_fetcher},
        url="https://youtube.com/watch?v=xx12345abcd",
        notion_name="Test Video",
    )
    assert result.success
    notion.write_triaged.assert_called_once()
    call_kwargs = notion.write_triaged.call_args.kwargs
    assert call_kwargs["status_after"] == "Fetching"
    assert call_kwargs["content_type"] == "YouTube"


def test_routed_writes_status_ready_for_tier_b(tmp_path: Path):
    db_path = tmp_path / "q.db"
    store = TriageQueueStore(db_path=str(db_path))
    notion = MagicMock()
    title_fetcher = MagicMock()
    title_fetcher.fetch_title.return_value = None
    result = _materialize(
        routed,
        partition_key="p-1",
        resources={"triage_notion": notion, "triage_store": store, "title_fetcher": title_fetcher},
        url="https://blog.example.com/post",
        notion_name="Some Article",
    )
    assert result.success
    call_kwargs = notion.write_triaged.call_args.kwargs
    assert call_kwargs["status_after"] == "Ready"
    assert call_kwargs["content_type"] == "Article"


def test_routed_persists_canonical_url_to_store_not_to_notion(tmp_path: Path):
    """Canonical URL (tracking params stripped) goes to local store.
    notion.write_triaged does not receive a canonical_url kwarg."""
    db_path = tmp_path / "q.db"
    store = TriageQueueStore(db_path=str(db_path))
    notion = MagicMock()
    title_fetcher = MagicMock()
    title_fetcher.fetch_title.return_value = None
    dirty_url = "https://example.com/p?utm_source=newsletter&id=42"
    result = _materialize(
        routed,
        partition_key="p-1",
        resources={"triage_notion": notion, "triage_store": store, "title_fetcher": title_fetcher},
        url=dirty_url,
        notion_name="Article",
    )
    assert result.success
    from domains.raw_store import queue as queue_db

    row = queue_db.get_row(db_path=db_path, notion_page_id="p-1")
    assert row is not None
    assert "utm_source" not in row["canonical_url"]
    assert "id=42" in row["canonical_url"]
    write_triaged_kwargs = notion.write_triaged.call_args.kwargs
    assert "canonical_url" not in write_triaged_kwargs
