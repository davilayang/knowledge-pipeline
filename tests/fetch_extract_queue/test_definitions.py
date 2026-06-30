"""Smoke test — the fetch_extract_queue pipeline loads cleanly.

Catches code-server crashes the user wouldn't see until `dagster dev` start.
"""

from orchestrators.defs.fetch_extract_queue import defs


def test_pipeline_defs_loads():
    asset_keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert asset_keys == {
        "fetch_extract_queue/fetched",
        "fetch_extract_queue/extracted",
        "fetch_extract_queue/published",
        "fetch_extract_queue/source_summary",
    }


def test_pipeline_has_one_job_named_fetch_extract_queue():
    job_names = {j.name for j in defs.jobs}
    assert job_names == {"fetch_extract_queue"}


def test_pipeline_exposes_poll_and_failure_sensors():
    sensor_names = {s.name for s in defs.sensors}
    assert sensor_names == {"poll_notion_for_extract", "mark_notion_failed_on_extract"}


def test_pipeline_registers_notion_lifecycle_check():
    spec_names = []
    for c in defs.asset_checks:
        spec_names.extend(s.name for s in c.check_specs)
    assert "notion_lifecycle_in_sync" in spec_names
