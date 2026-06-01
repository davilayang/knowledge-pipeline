"""Tests for triage_queued_items resources."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from domains.raw_store import queue as queue_db
from orchestrators.defs.triage_queued_items.resources import (
    TitleFetcherResource,
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


def test_triage_notion_write_triaged_writes_metadata_then_status():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(TriageNotionResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="YouTube",
            name_if_empty="My Video",
            status_after="Fetching",
        )
    assert fake_client.pages.update.call_count == 2
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    second_call_props = fake_client.pages.update.call_args_list[1].kwargs["properties"]
    assert "Content Type" in first_call_props
    assert "Name" in first_call_props
    assert list(second_call_props.keys()) == ["Status"]
    assert second_call_props["Status"]["select"]["name"] == "Fetching"


def test_triage_notion_write_triaged_truncates_long_name():
    resource = _make_notion()
    fake_client = MagicMock()
    long_name = "X" * 500
    with patch.object(TriageNotionResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            name_if_empty=long_name,
            status_after="Ready",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    written_name = first_call_props["Name"]["title"][0]["text"]["content"]
    assert len(written_name) == 200


def test_triage_notion_write_triaged_skips_name_when_none():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(TriageNotionResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            name_if_empty=None,
            status_after="Ready",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert "Name" not in first_call_props
    assert "Content Type" in first_call_props


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


# -------- TitleFetcherResource --------


def test_title_fetcher_returns_none_on_non_200():
    fetcher = TitleFetcherResource()
    fake_resp = MagicMock()
    fake_resp.status_code = 403
    with patch(
        "orchestrators.defs.triage_queued_items.resources.requests.get", return_value=fake_resp
    ):
        result = fetcher.fetch_title("https://example.com/blocked")
    assert result is None


def test_title_fetcher_extracts_title_tag():
    fetcher = TitleFetcherResource()
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = "<html><head><title>Hello World</title></head></html>"
    with patch(
        "orchestrators.defs.triage_queued_items.resources.requests.get", return_value=fake_resp
    ):
        result = fetcher.fetch_title("https://example.com/page")
    assert result == "Hello World"
