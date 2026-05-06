# Asset checks that gate the Drive flow on snapshot validity.
#
# blocking=True means a failed check stops downstream materialization in the
# same run — so a corrupt snapshot can never reach the upload step.

import sqlite3
from pathlib import Path

import dagster as dg

from .resources import BackupResource

MIN_SNAPSHOT_BYTES = 1024  # arbitrary tiny floor; an empty SQLite file is ~0–4KB


def _verify_one(
    context: dg.AssetCheckExecutionContext,
    backup: BackupResource,
    db_name: str,
) -> dg.AssetCheckResult:
    path = backup.get_partition_dir(context.partition_key) / db_name

    if not path.exists():
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"Snapshot file missing: {path}",
        )

    size = path.stat().st_size
    if size < MIN_SNAPSHOT_BYTES:
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"Snapshot suspiciously small: {size} bytes",
            metadata={"size_bytes": dg.MetadataValue.int(size)},
        )

    conn = sqlite3.connect(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        table_count = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table';"
        ).fetchone()[0]
    finally:
        conn.close()

    metadata = {
        "size_bytes": dg.MetadataValue.int(size),
        "integrity": dg.MetadataValue.text(integrity),
        "table_count": dg.MetadataValue.int(table_count),
    }

    if integrity != "ok":
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"PRAGMA integrity_check returned: {integrity}",
            metadata=metadata,
        )
    if table_count == 0:
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description="Snapshot has no tables",
            metadata=metadata,
        )

    return dg.AssetCheckResult(passed=True, metadata=metadata)


@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "raw_store"]),
    name="verify_snapshot_raw_store",
    blocking=True,
    description="raw_store.db opens, integrity_check passes, has tables.",
)
def verify_snapshot_raw_store(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    return _verify_one(context, backup, "raw_store.db")


@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "sessions"]),
    name="verify_snapshot_sessions",
    blocking=True,
    description="sessions.db opens, integrity_check passes, has tables.",
)
def verify_snapshot_sessions(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    return _verify_one(context, backup, "sessions.db")


all_checks = [verify_snapshot_raw_store, verify_snapshot_sessions]
