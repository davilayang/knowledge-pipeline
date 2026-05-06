"""Smoke tests for the backup_readings Dagster pipeline.

These run on every commit so asset-graph regressions (broken imports,
asset-key drift, dep-graph mistakes) fail in CI rather than at 03:00 UTC.
"""

from orchestrators.defs.pipelines.backup_readings import defs
from orchestrators.defs.pipelines.backup_readings.resources import (
    HealthcheckResource,
    RcloneResource,
)


def test_definitions_load_with_expected_shape():
    """Asset count, check count, sensor count, schedule names — all stable
    contract that the rest of the pipeline relies on."""
    asset_keys = {
        "/".join(k.path)
        for k in defs.resolve_asset_graph().get_all_asset_keys()
    }
    assert asset_keys == {
        "snapshots/raw_store",
        "snapshots/sessions",
        "google_drive/storage_capacity",
        "google_drive/uploaded_snapshots",
        "google_drive/pruned_old_backups",
        "local_disk/pruned_old_backups",
    }
    assert len(defs.asset_checks or []) == 2
    assert sorted(s.name for s in defs.sensors) == ["ping_healthcheck_on_success"]
    assert sorted(j.name for j in defs.jobs) == ["backup_readings"]
    assert sorted(s.name for s in defs.schedules) == ["run_daily_backup"]


def test_resource_is_configured_handles_whitespace():
    """Whitespace-only env values must read as unset; build_resources()
    .strip()s them, but assert the property does the right thing too."""
    assert RcloneResource(remote_name="").is_configured is False
    assert RcloneResource(remote_name="gdrive").is_configured is True

    assert HealthcheckResource(ping_url="").is_configured is False
    assert HealthcheckResource(ping_url="https://hc-ping.com/x").is_configured is True
