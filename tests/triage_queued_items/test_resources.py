"""Tests for triage_queued_items resources."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from domains.raw_store import queue as queue_db
from orchestrators.defs.triage_queued_items.resources import (
    TriageNotionResource,
    TriageQueueStore,
)

# -------- TriageNotionResource --------


def _make_notion() -> TriageNotionResource:
    return TriageNotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )


def test_triage_notion_query_queue_includes_empty_status_filter():
    resource = _make_notion()
    fake_client = MagicMock()
    fake_client.data_sources.query.return_value = {"results": []}
    with patch.object(TriageNotionResource, "_client", return_value=fake_client):
        resource.query_queue(page_size=5)
    call_filter = fake_client.data_sources.query.call_args.kwargs["filter"]
    assert call_filter["or"][0] == {"property": "Status", "select": {"equals": "Queued"}}
    assert call_filter["or"][1] == {"property": "Status", "select": {"is_empty": True}}


def test_triage_notion_write_triaged_writes_content_type_then_status():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(TriageNotionResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="YouTube",
            status_after="Fetching",
        )
    assert fake_client.pages.update.call_count == 2
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    second_call_props = fake_client.pages.update.call_args_list[1].kwargs["properties"]
    assert list(first_call_props.keys()) == ["Content Type"]
    assert list(second_call_props.keys()) == ["Status"]
    assert second_call_props["Status"]["select"]["name"] == "Fetching"


def test_triage_notion_write_triaged_never_touches_name():
    """Triage never writes Name — extract/NA fills it from real content."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(TriageNotionResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            status_after="Ready",
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Name" not in call.kwargs["properties"]


# -------- TriageQueueStore --------


def test_triage_queue_store_upsert_triaged_round_trips(tmp_path: Path):
    store = TriageQueueStore(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    store.upsert_triaged(
        notion_page_id="p-1",
        url="https://youtube.com/watch?v=abc123",
        canonical_url="https://youtube.com/watch?v=abc123",
        content_type="YouTube",
    )
    row = queue_db.get_row(db_path=tmp_path / "q.db", notion_page_id="p-1")
    assert row is not None
    assert row["content_type"] == "YouTube"
    assert row["canonical_url"] == "https://youtube.com/watch?v=abc123"
