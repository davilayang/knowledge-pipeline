"""Smoke tests for the backup_readings Dagster pipeline.

These run on every commit so asset-graph regressions (broken imports,
asset-key drift, dep-graph mistakes) fail in CI rather than at 03:00 UTC.
"""

from orchestrators.defs.backup_readings import defs
from orchestrators.defs.backup_readings.resources import BackupResource


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
        "session_store",
        "queue_store",
        "wiki_store",
        "snapshots/raw_store",
        "snapshots/sessions",
        "snapshots/notes",
        "snapshots/queue",
        "snapshots/wiki",
        "snapshots/wiki_pages",
        "google_drive/storage_capacity",
        "google_drive/uploaded_snapshots",
        "google_drive/pruned_old_backups",
        "local_disk/pruned_old_backups",
    }
    assert len(defs.asset_checks or []) == 6
    assert sorted(s.name for s in defs.sensors) == ["ping_healthcheck_on_success"]
    assert sorted(j.name for j in defs.jobs) == ["backup_readings"]
    assert sorted(s.name for s in defs.schedules) == ["run_daily_backup"]


def test_expected_files_cover_wiki_db_and_pages():
    """The upload completeness check is gated on `expected_files`; the kp-owned
    wiki snapshots must be in the expected set or they'd never be enforced."""
    backup = BackupResource(source_data_dir="/tmp/src", backup_dir="/tmp/dst")
    assert "wiki.db" in backup.expected_files
    assert "wiki.tgz" in backup.expected_files


def test_expected_files_track_the_renamed_articles_db():
    """newsletter-assistant renamed its articles store raw_store.db -> corpus.db
    in its 0.46.0 three-DB topology change (deployed 2026-07-11). The snapshot
    the backup writes is named after that source file, so `expected_files` — the
    set the Drive upload-completeness check is gated on — must name corpus.db.
    Leaving raw_store.db here let 41 consecutive nightly backups fail with
    "Source DB missing: /app/source/raw_store.db" while the schedule kept
    reporting successful ticks."""
    backup = BackupResource(source_data_dir="/tmp/src", backup_dir="/tmp/dst")
    assert "corpus.db" in backup.expected_files
    assert "raw_store.db" not in backup.expected_files
