"""Tests for poll_notion_queue + mark_notion_failed_on_run_failure."""

from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.extract_queued_items.def_config import (
    queue_items_partition_def,
)
from orchestrators.defs.extract_queued_items.sensors import (
    _handle_run_failure,
    poll_notion_queue,
)


def _notion_row(page_id: str, url: str, last_edited: str = "2026-05-31T10:00:00.000Z") -> dict:
    return {
        "id": page_id,
        "last_edited_time": last_edited,
        "properties": {"URL": {"url": url}},
    }


def test_poll_notion_queue_emits_one_run_request_per_queued_row():
    notion = MagicMock()
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
    ]
    context = dg.build_sensor_context()
    result = poll_notion_queue(context, notion=notion)
    assert isinstance(result, dg.SensorResult)
    assert {req.partition_key for req in result.run_requests} == {"p-1", "p-2"}
    notion.query_queue.assert_called_once_with(status="Queued", page_size=5)


def test_poll_notion_queue_skips_rows_with_empty_url():
    notion = MagicMock()
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        {"id": "p-2", "last_edited_time": "x", "properties": {"URL": {"url": None}}},
    ]
    result = poll_notion_queue(dg.build_sensor_context(), notion=notion)
    assert [req.partition_key for req in result.run_requests] == ["p-1"]


def test_poll_notion_queue_passes_url_in_run_tag():
    notion = MagicMock()
    notion.query_queue.return_value = [_notion_row("p-1", "https://example.com/a")]
    result = poll_notion_queue(dg.build_sensor_context(), notion=notion)
    assert result.run_requests[0].tags == {
        "notion_page_id": "p-1",
        "url": "https://example.com/a",
    }


def test_poll_notion_queue_builds_dynamic_partitions_request_for_new_page_ids():
    notion = MagicMock()
    notion.query_queue.return_value = [_notion_row("p-1", "https://example.com/a")]
    result = poll_notion_queue(dg.build_sensor_context(), notion=notion)
    assert len(result.dynamic_partitions_requests) == 1
    add_req = result.dynamic_partitions_requests[0]
    assert add_req.partitions_def_name == queue_items_partition_def.name
    assert add_req.partition_keys == ["p-1"]


def test_poll_notion_queue_returns_empty_dynamic_request_when_no_rows():
    notion = MagicMock()
    notion.query_queue.return_value = []
    result = poll_notion_queue(dg.build_sensor_context(), notion=notion)
    assert result.run_requests == []
    assert result.dynamic_partitions_requests == []


def test_poll_notion_queue_run_key_includes_last_edited_for_re_runnability():
    notion = MagicMock()
    notion.query_queue.return_value = [
        _notion_row("p-1", "https://example.com/a", last_edited="2026-05-31T10:00:00.000Z"),
    ]
    result = poll_notion_queue(dg.build_sensor_context(), notion=notion)
    assert result.run_requests[0].run_key == "queue-p-1-2026-05-31T10:00:00.000Z"


def test_handle_run_failure_writes_to_notion_with_run_tag_page_id():
    notion = MagicMock()
    _handle_run_failure(
        run_tags={"notion_page_id": "p-1"},
        failure_message="Fetched content under floor: 100 chars",
        notion=notion,
    )
    notion.update_status_failed.assert_called_once_with(
        "p-1", "Fetched content under floor: 100 chars"
    )


def test_handle_run_failure_falls_back_to_default_message():
    notion = MagicMock()
    _handle_run_failure(run_tags={"notion_page_id": "p-1"}, failure_message=None, notion=notion)
    notion.update_status_failed.assert_called_once_with("p-1", "run failed")


def test_handle_run_failure_noop_when_run_lacks_page_id_tag():
    notion = MagicMock()
    _handle_run_failure(run_tags={}, failure_message="x", notion=notion)
    notion.update_status_failed.assert_not_called()
