"""Tests for poll_notion_for_triage + mark_notion_failed_on_triage."""

from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.triage_queued_items.sensors import (
    _handle_run_failure,
    poll_notion_for_triage,
)


def _notion_row(
    page_id: str,
    url: str,
    last_edited: str = "2026-06-01T10:00:00.000Z",
    notion_name: str = "",
) -> dict:
    title_chunks = [{"plain_text": notion_name}] if notion_name else []
    return {
        "id": page_id,
        "last_edited_time": last_edited,
        "properties": {
            "URL": {"url": url},
            "Name": {"title": title_chunks},
        },
    }


def test_sensor_emits_run_request_per_row():
    notion = MagicMock()
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
        _notion_row("p-3", "https://youtube.com/watch?v=xyz"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert isinstance(result, dg.SensorResult)
    assert {req.partition_key for req in result.run_requests} == {"p-1", "p-2", "p-3"}


def test_sensor_skips_rows_with_empty_url():
    notion = MagicMock()
    notion.query_queue.return_value = [
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
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert len(result.dynamic_partitions_requests) == 1
    add_req = result.dynamic_partitions_requests[0]
    assert set(add_req.partition_keys) == {"p-1", "p-2"}


def test_sensor_carries_url_and_notion_name_in_run_config():
    notion = MagicMock()
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a", notion_name="My Article"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    ops_config = result.run_requests[0].run_config["ops"]
    classified_cfg = ops_config["triage_queued_items__classified"]["config"]
    routed_cfg = ops_config["triage_queued_items__routed"]["config"]
    assert classified_cfg == {"url": "https://example.com/a", "notion_name": "My Article"}
    assert routed_cfg == {"url": "https://example.com/a", "notion_name": "My Article"}


def test_sensor_tags_carry_only_notion_page_id():
    notion = MagicMock()
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a", notion_name="X"),
    ]
    result = poll_notion_for_triage(dg.build_sensor_context(), triage_notion=notion)
    assert result.run_requests[0].tags == {"notion_page_id": "p-1"}


def test_run_failure_helper_writes_notion_failed():
    notion = MagicMock()
    _handle_run_failure(
        run_tags={"notion_page_id": "p-1"},
        failure_message="classification error",
        triage_notion=notion,
    )
    notion.update_status_failed.assert_called_once_with("p-1", "classification error")
