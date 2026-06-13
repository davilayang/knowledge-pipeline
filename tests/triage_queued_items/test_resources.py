"""Tests for triage_queued_items resources.

The actual resource classes (`NotionQueueResource` and `QueueStoreResource`)
live in `orchestrators.defs.shared.queue_resources`; triage's `resources.py`
only binds them to per-pipeline keys. These tests assert the triage-relevant
behaviour of the shared classes (query filter shape, write_triaged contract).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from domains.queue_store import sources as queue_db
from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

# -------- NotionQueueResource (triage surface) --------


def _make_notion() -> NotionQueueResource:
    return NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )


def test_triage_notion_query_for_triage_filters_status_queued():
    """Native status type defaults new rows to Queued, so the filter is a
    single Status=Queued equality — no is_empty OR-branch needed."""
    resource = _make_notion()
    fake_client = MagicMock()
    fake_client.data_sources.query.return_value = {"results": []}
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.query_for_triage(page_size=5)
    call_filter = fake_client.data_sources.query.call_args.kwargs["filter"]
    assert call_filter == {"property": "Status", "status": {"equals": "Queued"}}


def test_triage_notion_write_triaged_writes_content_type_then_status():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="YouTube",
            canonical_url="https://youtu.be/abc",
            status_after="Fetching",
        )
    assert fake_client.pages.update.call_count == 2
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    second_call_props = fake_client.pages.update.call_args_list[1].kwargs["properties"]
    assert "Content Type" in first_call_props
    assert "Status" not in first_call_props
    assert list(second_call_props.keys()) == ["Status"]
    assert second_call_props["Status"]["status"]["name"] == "Fetching"


def test_triage_notion_write_triaged_writes_canonical_url():
    """Canonical URL is batched into the first (non-Status) call. Written as a
    Notion `rich_text` payload (not a `url` payload) — the Notion property is
    intentionally a text property so it doesn't appear in Web Clipper's
    URL-property candidate pool. That leaves `URL` as the only URL-type
    property, so mobile captures always land the page URL there."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="YouTube",
            canonical_url="https://youtu.be/abc",
            status_after="Fetching",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert first_call_props["Canonical URL"] == {
        "rich_text": [{"text": {"content": "https://youtu.be/abc"}}]
    }


def test_triage_notion_write_triaged_writes_added_at_when_provided():
    """added_at_iso → batched into the first (non-Status) call as a Notion
    date property."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            added_at_iso="2026-06-02T08:21:00.000Z",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert first_call_props["Added At"] == {"date": {"start": "2026-06-02T08:21:00.000Z"}}


def test_triage_notion_write_triaged_omits_added_at_when_not_provided():
    """added_at_iso=None → no Added At write. Preserves whatever Notion has."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            added_at_iso=None,
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Added At" not in call.kwargs["properties"]


def test_triage_notion_write_triaged_omits_name_when_not_provided():
    """name=None → no Name write. Preserves existing user-set value."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            name=None,
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Name" not in call.kwargs["properties"]


def test_triage_notion_write_triaged_writes_name_when_provided():
    """name=<str> → batched into the same Notion call as Content Type."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            name="Hello World",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert "Name" in first_call_props
    title_chunks = first_call_props["Name"]["title"]
    assert title_chunks[0]["text"]["content"] == "Hello World"


def test_triage_notion_write_triaged_writes_description_when_provided():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            description="A short blurb.",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert "Description" in first_call_props
    rich_text = first_call_props["Description"]["rich_text"]
    assert rich_text[0]["text"]["content"] == "A short blurb."


def test_triage_notion_write_triaged_omits_description_when_not_provided():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            description=None,
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Description" not in call.kwargs["properties"]


def test_triage_notion_write_triaged_writes_content_shape_when_classified():
    """content_shape="conference_talk" → batched into the first call as a
    SELECT property, same shape as Content Type."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="YouTube",
            content_shape="conference_talk",
            canonical_url="https://youtu.be/abc",
            status_after="Fetching",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert first_call_props["Content Shape"] == {"select": {"name": "conference_talk"}}


def test_triage_notion_write_triaged_omits_content_shape_when_unknown():
    """content_shape="unknown" → no Notion write. Leaves the property blank so
    users can pre-populate it as an override without triage stomping the value."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            content_shape="unknown",
            canonical_url="https://example.com",
            status_after="Fetching",
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Content Shape" not in call.kwargs["properties"]


def test_triage_notion_write_triaged_omits_content_shape_when_not_provided():
    """content_shape=None (default) → no Notion write. Backwards-compatible for
    callers from before Phase 4."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Fetching",
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Content Shape" not in call.kwargs["properties"]


def test_triage_notion_write_triaged_strips_name_whitespace_and_newlines():
    """Name with leading/trailing whitespace + newlines → stripped before Notion."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            name="\n  Hello World  \n",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert first_call_props["Name"]["title"][0]["text"]["content"] == "Hello World"


def test_triage_notion_write_triaged_strips_description_whitespace():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            description="  \n A blurb. \n  ",
        )
    first_call_props = fake_client.pages.update.call_args_list[0].kwargs["properties"]
    assert first_call_props["Description"]["rich_text"][0]["text"]["content"] == "A blurb."


def test_triage_notion_write_triaged_skips_name_when_strips_to_empty():
    """Name that's only whitespace → don't write Name at all (would blank the
    user's existing title)."""
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            name="   \n  ",
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Name" not in call.kwargs["properties"]


def test_triage_notion_write_triaged_skips_description_when_strips_to_empty():
    resource = _make_notion()
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.write_triaged(
            page_id="p-1",
            content_type="Article",
            canonical_url="https://example.com",
            status_after="Ready",
            description="\n\n  ",
        )
    for call in fake_client.pages.update.call_args_list:
        assert "Description" not in call.kwargs["properties"]


# -------- QueueStoreResource --------


def test_triage_queue_store_upsert_triaged_round_trips(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
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
