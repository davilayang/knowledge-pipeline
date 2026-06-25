# Asset checks that gate the Drive flow on snapshot validity.
#
# blocking=True means a failed check stops downstream materialization in the
# same run — so a corrupt snapshot can never reach the upload step.

import sqlite3
import tarfile

import dagster as dg

from .def_config import MIN_SNAPSHOT_BYTES
from .resources import BackupResource


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
    size_kb = size / 1024
    if size < MIN_SNAPSHOT_BYTES:
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"Snapshot suspiciously small: {size_kb:.2f} KB",
            metadata={"size_kb": dg.MetadataValue.float(size_kb)},
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
        "size_kb": dg.MetadataValue.float(size_kb),
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


def _verify_one_archive(
    context: dg.AssetCheckExecutionContext,
    backup: BackupResource,
    archive_name: str,
) -> dg.AssetCheckResult:
    path = backup.get_partition_dir(context.partition_key) / archive_name

    if not path.exists():
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"Archive missing: {path}",
        )

    size = path.stat().st_size
    size_kb = size / 1024
    if size < MIN_SNAPSHOT_BYTES:
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"Archive suspiciously small: {size_kb:.2f} KB",
            metadata={"size_kb": dg.MetadataValue.float(size_kb)},
        )

    try:
        with tarfile.open(path, "r:gz") as tf:
            member_count = sum(1 for m in tf if m.isfile())
    except (tarfile.TarError, OSError) as exc:
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description=f"Archive failed to open as gzip-tar: {exc}",
            metadata={"size_kb": dg.MetadataValue.float(size_kb)},
        )

    metadata = {
        "size_kb": dg.MetadataValue.float(size_kb),
        "member_count": dg.MetadataValue.int(member_count),
    }
    if member_count == 0:
        return dg.AssetCheckResult(
            passed=False,
            severity=dg.AssetCheckSeverity.ERROR,
            description="Archive contains no files",
            metadata=metadata,
        )
    return dg.AssetCheckResult(passed=True, metadata=metadata)


@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "notes"]),
    name="verify_snapshot_notes",
    blocking=True,
    description="notes.tgz opens as gzip-tar and contains at least one file.",
)
def verify_snapshot_notes(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    return _verify_one_archive(context, backup, "notes.tgz")


@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "queue"]),
    name="verify_snapshot_queue",
    blocking=True,
    description="queue.db opens, integrity_check passes, has tables.",
)
def verify_snapshot_queue(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    return _verify_one(context, backup, "queue.db")


@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "wiki"]),
    name="verify_snapshot_wiki",
    blocking=True,
    description="wiki.db opens, integrity_check passes, has tables.",
)
def verify_snapshot_wiki(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    return _verify_one(context, backup, "wiki.db")


@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "wiki_pages"]),
    name="verify_snapshot_wiki_pages",
    blocking=True,
    description="wiki.tgz opens as gzip-tar and contains at least one file.",
)
def verify_snapshot_wiki_pages(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    return _verify_one_archive(context, backup, "wiki.tgz")


all_checks = [
    verify_snapshot_raw_store,
    verify_snapshot_sessions,
    verify_snapshot_notes,
    verify_snapshot_queue,
    verify_snapshot_wiki,
    verify_snapshot_wiki_pages,
]
