"""Smoke tests — triage_queued_items pipeline loads cleanly."""

from orchestrators.defs.triage_queued_items import defs


def test_defs_loads_without_error():
    assert defs is not None


def test_defs_includes_all_assets():
    asset_keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert asset_keys == {
        "triage_queued_items/classified",
        "triage_queued_items/routed",
    }


def test_defs_includes_sensors():
    sensor_names = {s.name for s in defs.sensors}
    assert "poll_notion_for_triage" in sensor_names
    assert "mark_notion_failed_on_triage_failure" in sensor_names


def test_defs_includes_job():
    job_names = {j.name for j in defs.jobs}
    assert "triage_queued_items" in job_names
