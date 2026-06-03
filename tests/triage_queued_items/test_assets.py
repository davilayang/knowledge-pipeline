"""Tests for the triaged asset."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest
from orchestrators.defs.shared.queue_resources import QueueStoreResource
from orchestrators.defs.triage_queued_items.assets import triaged
from orchestrators.defs.triage_queued_items.def_config import queue_items_partition_def
from orchestrators.defs.triage_queued_items.url_meta import UrlMeta


@pytest.fixture(autouse=True)
def _no_network():
    """Default: url_meta returns empty UrlMeta with final_url = input. Tests
    that want a specific UrlMeta should override via _patch_fetch."""

    def _empty(url: str, **kwargs):
        return UrlMeta(final_url=url, title=None, description=None)

    with patch(
        "orchestrators.defs.triage_queued_items.assets.fetch_url_meta",
        side_effect=_empty,
    ) as fake:
        yield fake


def _patch_fetch(meta: UrlMeta):
    return patch(
        "orchestrators.defs.triage_queued_items.assets.fetch_url_meta",
        return_value=meta,
    )


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
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
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


def test_triaged_writes_fetched_title_to_notion_when_config_name_empty(tmp_path: Path):
    """config.name unset + fetched title present → triage seeds Name in Notion."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        final_url="https://blog.example.com/post",
        title="Fetched Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") == "Fetched Title"


def test_triaged_does_not_overwrite_existing_name(tmp_path: Path):
    """config.name set → triage leaves Notion's Name alone, regardless of fetch."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        final_url="https://blog.example.com/post",
        title="Fetched Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
            name="User-set Name",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None


def test_triaged_writes_fetched_description_to_notion(tmp_path: Path):
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        final_url="https://blog.example.com/post",
        title=None,
        description="A short blurb of the post.",
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("description") == "A short blurb of the post."


def test_triaged_succeeds_with_empty_meta_on_fetch_failure(tmp_path: Path):
    """fetch_url_meta returning empty meta (network error, non-html, etc.) →
    triage classifies + writes Status normally, just without name/description."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None
    assert kwargs.get("description") is None
    assert kwargs["status_after"] == "Ready"


def test_triaged_uses_final_url_for_classification_after_redirect(tmp_path: Path):
    """A click-tracker URL that redirects to youtube.com → classified as YouTube,
    not Article. The redirect resolution happens in url_meta; triage consumes
    final_url for downstream classification + canonicalization."""
    resources, _ = _resources(tmp_path)
    meta = UrlMeta(
        final_url="https://youtube.com/watch?v=abc123",
        title=None,
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://t.co/shortened",
        )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"
    assert metadata["tier"].text == "A"


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
    """name is metadata-only — appears on the run; Notion's Name is not
    overwritten when config.name is set."""
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
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None


def test_triaged_persists_canonical_url_to_store_and_notion(tmp_path: Path):
    """Canonical URL goes to both queue.db (NA reads it for kp_queue_cache)
    and Notion's `Canonical URL` field (UI-visible debug surface, text-typed
    on purpose — see write_triaged docstring)."""
    resources, notion = _resources(tmp_path)
    dirty_url = "https://example.com/p?utm_source=newsletter&id=42"
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url=dirty_url,
    )
    assert result.success
    from domains.queue_store import sources as queue_db

    row = queue_db.get_row(db_path=resources["triage_store"].db_path, notion_page_id="p-1")
    assert row is not None
    assert row["canonical_url"] == "https://example.com/p"
    assert notion.write_triaged.call_args.kwargs["canonical_url"] == "https://example.com/p"


def test_triaged_passes_added_at_iso_through_to_notion(tmp_path: Path):
    """added_at_iso from TriageInput → forwarded to write_triaged unchanged."""
    resources, notion = _resources(tmp_path)
    instance = _instance_with_partition("p-1")
    result = dg.materialize(
        [triaged],
        partition_key="p-1",
        resources=resources,
        instance=instance,
        tags={"notion_page_id": "p-1"},
        run_config={
            "ops": {
                "triage_queued_items__triaged": {
                    "config": {
                        "url": "https://example.com/post",
                        "added_at_iso": "2026-06-02T08:21:00.000Z",
                    }
                }
            }
        },
    )
    assert result.success
    assert notion.write_triaged.call_args.kwargs["added_at_iso"] == "2026-06-02T08:21:00.000Z"


def test_triaged_passes_none_added_at_when_unset(tmp_path: Path):
    """No added_at_iso in run_config → None forwarded to write_triaged."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/post",
    )
    assert result.success
    assert notion.write_triaged.call_args.kwargs["added_at_iso"] is None
