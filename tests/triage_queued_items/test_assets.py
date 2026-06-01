"""Tests for the triaged asset."""

from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.triage_queued_items.assets import triaged
from orchestrators.defs.triage_queued_items.def_config import queue_items_partition_def
from orchestrators.defs.triage_queued_items.resources import TriageQueueStore


def _instance_with_partition(page_id: str) -> dg.DagsterInstance:
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(queue_items_partition_def.name, [page_id])
    return instance


def _materialize(
    *,
    partition_key: str,
    resources: dict,
    url: str,
    content_type: str | None = None,
    name: str | None = None,
):
    instance = _instance_with_partition(partition_key)
    op_config: dict = {"url": url}
    if content_type is not None:
        op_config["content_type"] = content_type
    if name is not None:
        op_config["name"] = name
    return dg.materialize(
        [triaged],
        partition_key=partition_key,
        resources=resources,
        instance=instance,
        tags={"notion_page_id": partition_key},
        run_config={"ops": {"triage_queued_items__triaged": {"config": op_config}}},
    )


def _get_metadata(result) -> dict:
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    assert mat_events
    return mat_events[0].materialization.metadata


def _resources(tmp_path: Path):
    store = TriageQueueStore(db_path=str(tmp_path / "q.db"))
    notion = MagicMock()
    return {"triage_notion": notion, "triage_store": store}, notion


# -------- classification metadata --------


def test_triaged_returns_youtube_for_youtube_url(tmp_path: Path):
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://youtube.com/watch?v=xx12345abcd",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"
    assert metadata["tier"].text == "A"


def test_triaged_returns_article_for_blog_url(tmp_path: Path):
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "Article"
    assert metadata["tier"].text == "B"


# -------- routing side effects --------


def test_triaged_writes_status_fetching_for_tier_a(tmp_path: Path):
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://youtube.com/watch?v=xx12345abcd",
    )
    assert result.success
    notion.write_triaged.assert_called_once()
    call_kwargs = notion.write_triaged.call_args.kwargs
    assert call_kwargs["status_after"] == "Fetching"
    assert call_kwargs["content_type"] == "YouTube"


def test_triaged_writes_status_ready_for_tier_b(tmp_path: Path):
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    call_kwargs = notion.write_triaged.call_args.kwargs
    assert call_kwargs["status_after"] == "Ready"
    assert call_kwargs["content_type"] == "Article"


def test_triaged_does_not_write_name_to_notion(tmp_path: Path):
    """We no longer fetch or write titles — Notion's Name property is left
    untouched. Downstream extract (Tier A) or NA-at-engagement (Tier B)
    fills it from real content."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert "name_if_empty" not in kwargs
    assert "name" not in kwargs


# -------- user override --------


def test_triaged_respects_user_set_content_type(tmp_path: Path):
    """A blog-looking URL with content_type=YouTube override → treated as YouTube."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
        content_type="YouTube",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"
    assert metadata["content_type_source"].text == "notion"
    assert metadata["tier"].text == "A"  # YouTube is Tier A
    assert notion.write_triaged.call_args.kwargs["status_after"] == "Fetching"


def test_triaged_falls_back_to_classifier_on_typo_content_type(tmp_path: Path):
    """User typo'd content_type → classifier wins, source = 'classified'."""
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://youtube.com/watch?v=abc",
        content_type="Youtub",  # typo
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"  # classifier from URL
    assert metadata["content_type_source"].text == "classified"


def test_triaged_falls_back_to_classifier_on_empty_content_type(tmp_path: Path):
    """No content_type override → classify from URL."""
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://arxiv.org/abs/2401.12345",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "arXiv"
    assert metadata["content_type_source"].text == "classified"


def test_triaged_passes_name_through_to_metadata(tmp_path: Path):
    """name is metadata-only — appears on the run, doesn't touch Notion."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/post",
        name="A great article",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["name"].text == "A great article"
    # Sanity: Notion.write_triaged still does not receive a name
    kwargs = notion.write_triaged.call_args.kwargs
    assert "name" not in kwargs and "name_if_empty" not in kwargs


def test_triaged_persists_canonical_url_to_store_not_to_notion(tmp_path: Path):
    """Canonical URL (tracking params stripped) goes to local store.
    notion.write_triaged does not receive a canonical_url kwarg."""
    resources, notion = _resources(tmp_path)
    dirty_url = "https://example.com/p?utm_source=newsletter&id=42"
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url=dirty_url,
    )
    assert result.success
    from domains.raw_store import queue as queue_db

    row = queue_db.get_row(db_path=resources["triage_store"].db_path, notion_page_id="p-1")
    assert row is not None
    assert "utm_source" not in row["canonical_url"]
    assert "id=42" in row["canonical_url"]
    write_triaged_kwargs = notion.write_triaged.call_args.kwargs
    assert "canonical_url" not in write_triaged_kwargs
