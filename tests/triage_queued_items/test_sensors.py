"""Tests for poll_notion_for_triage + mark_notion_failed_on_triage."""

from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.triage_queued_items.sensors import poll_notion_for_triage


def _notion_row(
    page_id: str,
    url: str,
    *,
    last_edited: str = "2026-06-01T10:00:00.000Z",
    created_time: str = "2026-06-01T09:00:00.000Z",
    content_type: str | None = None,
    name: str = "",
    added_at: str | None = None,
    use_page_body: bool | None = None,
) -> dict:
    props: dict = {"URL": {"url": url}}
    if content_type is not None:
        props["Content Type"] = {"select": {"name": content_type}}
    if name:
        props["Name"] = {"title": [{"plain_text": name}]}
    if added_at is not None:
        props["Added At"] = {"date": {"start": added_at}}
    if use_page_body is not None:
        props["Use page body as content"] = {"checkbox": use_page_body}
    return {
        "id": page_id,
        "last_edited_time": last_edited,
        "created_time": created_time,
        "properties": props,
    }


def test_sensor_emits_run_request_per_row():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
        _notion_row("p-3", "https://youtube.com/watch?v=xyz"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert isinstance(result, dg.SensorResult)
    assert {req.partition_key for req in result.run_requests} == {"p-1", "p-2", "p-3"}


def test_sensor_skips_rows_with_empty_url():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        {
            "id": "p-2",
            "last_edited_time": "x",
            "properties": {"URL": {"url": None}, "Name": {"title": []}},
        },
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert [req.partition_key for req in result.run_requests] == ["p-1"]


def test_sensor_registers_dynamic_partition_per_row():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert len(result.dynamic_partitions_requests) == 1
    add_req = result.dynamic_partitions_requests[0]
    assert set(add_req.partition_keys) == {"p-1", "p-2"}


def test_sensor_carries_url_in_run_config():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    ops_config = result.run_requests[0].run_config["ops"]
    triaged_cfg = ops_config["triage_queued_items__triaged"]["config"]
    assert triaged_cfg["url"] == "https://example.com/a"
    # Unset overrides serialize as either absent or None — both mean "use fallback"
    assert triaged_cfg.get("content_type") is None
    assert triaged_cfg.get("name") is None


def test_sensor_reads_user_set_content_type():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a", content_type="Podcast"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg["content_type"] == "Podcast"


def test_sensor_reads_user_set_name():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a", name="My Article"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg["name"] == "My Article"


def test_sensor_backfills_added_at_from_created_time_when_missing():
    """Mobile capture often omits Added At; sensor backfills from
    Notion's created_time so the row gets a sane chronological anchor."""
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row(
            "p-1",
            "https://example.com/a",
            created_time="2026-06-02T08:21:00.000Z",
            added_at=None,
        ),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg["added_at_iso"] == "2026-06-02T08:21:00.000Z"


def test_sensor_preserves_existing_added_at():
    """Row already has Added At → sensor leaves it alone (added_at_iso=None)."""
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row(
            "p-1",
            "https://example.com/a",
            created_time="2026-06-02T08:21:00.000Z",
            added_at="2026-05-30T12:00:00.000Z",
        ),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg.get("added_at_iso") is None


def test_sensor_tags_carry_only_notion_page_id():
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert result.run_requests[0].tags == {"notion_page_id": "p-1"}


def test_sensor_reads_use_page_body_checkbox_and_skips_block_fetch_when_false() -> None:
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a", use_page_body=False),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    notion.get_page_body_markdown.assert_not_called()
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg.get("raw_content_override", "") == ""


def test_sensor_defaults_use_page_body_to_false_when_unset() -> None:
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    notion.get_page_body_markdown.assert_not_called()
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg.get("raw_content_override", "") == ""


def test_sensor_fetches_blocks_and_converts_when_checkbox_set() -> None:
    notion = MagicMock()
    notion.query_for_triage.return_value = [
        _notion_row("p-1", "https://example.com/a", use_page_body=True),
    ]
    notion.get_page_body_markdown.return_value = "# Pasted\n\nlong body content."
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    notion.get_page_body_markdown.assert_called_once_with("p-1")
    triaged_cfg = result.run_requests[0].run_config["ops"]["triage_queued_items__triaged"]["config"]
    assert triaged_cfg["raw_content_override"] == "# Pasted\n\nlong body content."
