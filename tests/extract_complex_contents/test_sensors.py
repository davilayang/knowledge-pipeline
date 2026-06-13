"""Tests for poll_notion_for_extract + mark_notion_failed_on_extract."""

from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.extract_complex_contents.def_config import (
    MAX_TO_EXTRACT_PER_TICK,
    SUPPORTED_CONTENT_TYPES,
)
from orchestrators.defs.extract_complex_contents.sensors import poll_notion_for_extract


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


def test_sensor_registers_dynamic_partitions_for_self_heal():
    """Self-heal against orphan partitions: if a Notion row is at
    Status=Fetching but its dynamic partition was lost (DAGSTER_HOME reset)
    or never existed (re-deploy), the sensor must register it before the
    RunRequest. Otherwise the run launch crashes with
    DagsterUnknownPartitionError. Triage's pre-registration alone isn't
    enough — this self-heal closes the gap."""
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://example.com/a"),
        _notion_row("p-2", "https://example.com/b"),
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert len(result.dynamic_partitions_requests) == 1
    assert set(result.dynamic_partitions_requests[0].partition_keys) == {"p-1", "p-2"}


def test_sensor_emits_no_partition_requests_when_no_rows():
    """No Fetching rows → no run requests AND no partition-add requests
    (empty list, not an add-request with empty keys)."""
    notion = MagicMock()
    notion.query_for_extract.return_value = []
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.run_requests == []
    assert result.dynamic_partitions_requests == []


def test_sensor_run_request_carries_content_type_tag():
    notion = MagicMock()
    notion.query_for_extract.return_value = [
        _notion_row("p-1", "https://youtube.com/watch?v=abc", content_type="YouTube")
    ]
    result = poll_notion_for_extract(dg.build_sensor_context(), notion=notion)
    assert result.run_requests[0].tags["content_type"] == "YouTube"
