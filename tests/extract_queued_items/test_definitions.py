"""Smoke test — the extract_queued_items pipeline loads cleanly.

Catches code-server crashes the user wouldn't see until `dagster dev` start.
"""

from orchestrators.defs.extract_queued_items import defs


def test_pipeline_defs_loads():
    asset_keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert asset_keys == {
        "extract_queued_items/fetched_content",
        "extract_queued_items/topic_card",
        "extract_queued_items/persisted",
    }


def test_pipeline_has_one_job_named_extract_queued_items():
    job_names = {j.name for j in defs.jobs}
    assert job_names == {"extract_queued_items"}


def test_pipeline_exposes_poll_and_failure_sensors():
    sensor_names = {s.name for s in defs.sensors}
    assert sensor_names == {"poll_notion_queue", "mark_notion_failed_on_run_failure"}


def test_pipeline_registers_notion_lifecycle_check():
    spec_names = []
    for c in defs.asset_checks:
        spec_names.extend(s.name for s in c.check_specs)
    assert "notion_lifecycle_in_sync" in spec_names
