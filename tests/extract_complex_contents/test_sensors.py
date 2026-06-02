"""Tests for poll_notion_for_extract + mark_notion_failed_on_extract."""

from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.extract_complex_contents.def_config import (
    MAX_TO_EXTRACT_PER_TICK,
    SUPPORTED_CONTENT_TYPES,
)
from orchestrators.defs.extract_complex_contents.sensors import (
    _handle_run_failure,
    poll_notion_for_extract,
)


def _notion_row(
    page_id: str,
    url: str,
    last_edited: str = "2026-05-31T10:00:00.000Z",
    content_type: str = "YouTube",
) -> dict:
    return {
        "id": page_id,
        "last_edited_time": last_edited,
        "properties": {
            "URL": {"url": url},
            "Content Type": {"select": {"name": content_type}},
        },
    }


def test_poll_notion_for_extract_emits_one_run_request_per_fetching_row():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
    ]
    context = dg.build_sensor_context()
    result = poll_notion_for_extract(context, notion=notion)
    assert isinstance(result, dg.SensorResult)
    assert {req.partition_key for req in result.run_requests} == {"p-1", "p-2"}
    notion.query_for_extract.assert_called_once_with(
        page_size=MAX_TO_EXTRACT_PER_TICK,
        supported_content_types=SUPPORTED_CONTENT_TYPES,
    )


def test_poll_notion_for_extract_skips_rows_with_empty_url():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        {
            "id": "p-2",
            "last_edited_time": "x",
            "properties": {
                "URL": {"url": None},
                "Content Type": {"select": {"name": "YouTube"}},
            },
        },
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert [req.partition_key for req in result.run_requests] == ["p-1"]


def test_poll_notion_for_extract_skips_rows_with_missing_content_type():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        {
            "id": "p-2",
            "last_edited_time": "x",
            "properties": {
                "URL": {"url": "https://example.com/b"},
                "Content Type": {"select": None},
            },
        },
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert [req.partition_key for req in result.run_requests] == ["p-1"]


def test_poll_notion_for_extract_passes_url_and_content_type_in_run_tag():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://example.com/a", content_type="YouTube")
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.run_requests[0].tags == {
        "notion_page_id": "p-1",
        "url": "https://example.com/a",
        "content_type": "YouTube",
    }


def test_poll_notion_for_extract_returns_empty_when_no_rows():
    notion = MagicMock()
    notion.query_for_extract.return_value = []
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.run_requests == []


def test_poll_notion_for_extract_run_key_includes_last_edited_for_re_runnability():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://example.com/a", last_edited="2026-05-31T10:00:00.000Z"),
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.run_requests[0].run_key == "queue-p-1-2026-05-31T10:00:00.000Z"


def test_sensor_does_not_register_dynamic_partitions():
    notion = MagicMock()
    notion.query_for_extract.return_value = [_notion_row("p-1", "https://example.com/a")]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.dynamic_partitions_requests == []


def test_sensor_run_request_carries_content_type_tag():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://youtube.com/watch?v=abc", content_type="YouTube")
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.run_requests[0].tags["content_type"] == "YouTube"


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
