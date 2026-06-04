"""Smoke tests for the backup_readings Dagster pipeline.

These run on every commit so asset-graph regressions (broken imports,
asset-key drift, dep-graph mistakes) fail in CI rather than at 03:00 UTC.
"""

from orchestrators.defs.backup_readings import defs


def test_definitions_load_with_expected_shape():
    """Asset count, check count, sensor count, schedule names — all stable
    contract that the rest of the pipeline relies on."""
    asset_keys = {"/".join(k.path) for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert asset_keys == {
        # External upstreams referenced via deps. The full AssetSpecs live
        # in pipelines/upstream_sources.py and are merged at the parent
        # definitions level; backup_readings.defs alone sees them as
        # implicit external nodes auto-created from the dep references.
        "raw_store",
        "sessions",
        "research",
        "queue_store",
        "snapshots/raw_store",
        "snapshots/sessions",
        "snapshots/notes",
        "snapshots/research",
        "snapshots/queue",
        "google_drive/storage_capacity",
        "google_drive/uploaded_snapshots",
        "google_drive/pruned_old_backups",
        "local_disk/pruned_old_backups",
    }
    assert len(defs.asset_checks or []) == 5
    assert sorted(s.name for s in defs.sensors) == ["ping_healthcheck_on_success"]
    assert sorted(j.name for j in defs.jobs) == ["backup_readings"]
    assert sorted(s.name for s in defs.schedules) == ["run_daily_backup"]
