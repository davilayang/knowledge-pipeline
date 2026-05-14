# Daily-partitioned backup pipeline. See README.md for the DAG diagram.

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import dagster as dg

from orchestrators.config import BACKUP_READINGS_DAG_VERSION

from .def_config import (
    DRIVE_USAGE_THRESHOLD,
    MAX_DRIVE_BACKUPS,
    MAX_LOCAL_BACKUPS,
    PIPELINE_TAG,
    daily_partition_def,
)
from .resources import BackupResource, RcloneResource

# ---------- helpers ----------


def _sqlite_backup(source: Path, dest: Path) -> None:
    """Consistent snapshot via SQLite's online backup API."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_one_db(
    context: dg.AssetExecutionContext,
    backup: BackupResource,
    db_name: str,
) -> dg.MaterializeResult:
    source = backup.get_source_dir() / db_name
    if not source.exists():
        raise dg.Failure(
            description=f"Source DB missing: {source}",
            metadata={"source_path": dg.MetadataValue.path(str(source))},
        )

    dest = backup.get_partition_dir(context.partition_key) / db_name
    _sqlite_backup(source, dest)
    size = dest.stat().st_size
    digest = _sha256(dest)

    return dg.MaterializeResult(
        metadata={
            "size_mb": dg.MetadataValue.float(size / (1024 * 1024)),
            "sha256": dg.MetadataValue.text(digest),
            "source_path": dg.MetadataValue.path(str(source)),
            "dest_path": dg.MetadataValue.path(str(dest)),
        }
    )


def _snapshot_one_dir(
    context: dg.AssetExecutionContext,
    backup: BackupResource,
    source_subdir: str,
    archive_name: str,
) -> dg.MaterializeResult:
    source = backup.get_source_dir() / source_subdir
    if not source.is_dir():
        raise dg.Failure(
            description=f"Source dir missing: {source}",
            metadata={"source_path": dg.MetadataValue.path(str(source))},
        )

    dest = backup.get_partition_dir(context.partition_key) / archive_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(source, arcname=source.name)

    with tarfile.open(dest, "r:gz") as tf:
        member_count = sum(1 for m in tf if m.isfile())

    size = dest.stat().st_size
    digest = _sha256(dest)

    return dg.MaterializeResult(
        metadata={
            "size_mb": dg.MetadataValue.float(size / (1024 * 1024)),
            "member_count": dg.MetadataValue.int(member_count),
            "sha256": dg.MetadataValue.text(digest),
            "source_path": dg.MetadataValue.path(str(source)),
            "dest_path": dg.MetadataValue.path(str(dest)),
        }
    )


# ---------- snapshot assets ----------


@dg.asset(
    key=["snapshots", "raw_store"],
    group_name="backup",
    compute_kind="sqlite",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    deps=[dg.AssetDep("raw_store")],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description="Consistent SQLite snapshot of raw_store.db for the partition's date.",
)
def snapshot_raw_store(
    context: dg.AssetExecutionContext, backup: BackupResource
) -> dg.MaterializeResult:
    return _snapshot_one_db(context, backup, "raw_store.db")


@dg.asset(
    key=["snapshots", "sessions"],
    group_name="backup",
    compute_kind="sqlite",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    deps=[dg.AssetDep("sessions")],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description="Consistent SQLite snapshot of sessions.db for the partition's date.",
)
def snapshot_sessions(
    context: dg.AssetExecutionContext, backup: BackupResource
) -> dg.MaterializeResult:
    return _snapshot_one_db(context, backup, "sessions.db")


@dg.asset(
    key=["snapshots", "notes"],
    group_name="backup",
    compute_kind="file",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description="gzip-tar archive of newsletter-assistant/data/notes/ for the partition's date.",
)
def snapshot_notes(
    context: dg.AssetExecutionContext, backup: BackupResource
) -> dg.MaterializeResult:
    return _snapshot_one_dir(context, backup, "notes", "notes.tgz")


@dg.asset(
    key=["snapshots", "research"],
    group_name="backup",
    compute_kind="sqlite",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description="Consistent SQLite snapshot of research.db for the partition's date.",
)
def snapshot_research(
    context: dg.AssetExecutionContext, backup: BackupResource
) -> dg.MaterializeResult:
    return _snapshot_one_db(context, backup, "research.db")


# ---------- Drive capacity observation ----------


@dg.asset(
    key=["google_drive", "storage_capacity"],
    group_name="backup",
    compute_kind="googledrive",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    deps=[
        dg.AssetDep(["snapshots", "raw_store"]),
        dg.AssetDep(["snapshots", "sessions"]),
        dg.AssetDep(["snapshots", "notes"]),
        dg.AssetDep(["snapshots", "research"]),
    ],
    check_specs=[
        dg.AssetCheckSpec(
            name="drive_capacity_below_threshold",
            asset=dg.AssetKey(["google_drive", "storage_capacity"]),
            blocking=True,
            description=f"Fail when Drive used_pct > {DRIVE_USAGE_THRESHOLD:.0%}.",
        )
    ],
    description="Daily Drive usage observation; co-emits the threshold check.",
)
def storage_capacity(context: dg.AssetExecutionContext, rclone: RcloneResource):
    out = subprocess.run(
        ["rclone", "about", "--json", f"{rclone.remote_name}:"],
        capture_output=True,
        text=True,
        check=True,
    )
    quota = json.loads(out.stdout)
    total = int(quota.get("total", 0))
    used = int(quota.get("used", 0))
    used_pct = used / total if total else 0.0

    yield dg.MaterializeResult(
        metadata={
            "remote": dg.MetadataValue.text(rclone.remote_name),
            "used_gb": dg.MetadataValue.float(used / 1e9),
            "total_gb": dg.MetadataValue.float(total / 1e9),
            "used_pct": dg.MetadataValue.float(used_pct),
        }
    )

    passed = used_pct <= DRIVE_USAGE_THRESHOLD
    yield dg.AssetCheckResult(
        check_name="drive_capacity_below_threshold",
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR,
        description=(
            None
            if passed
            else (
                f"Drive remote '{rclone.remote_name}' at {used_pct:.1%} of "
                f"{total / 1e9:.1f} GB (threshold {DRIVE_USAGE_THRESHOLD:.0%})."
            )
        ),
        metadata={
            "used_pct": dg.MetadataValue.float(used_pct),
            "threshold": dg.MetadataValue.float(DRIVE_USAGE_THRESHOLD),
        },
    )


# ---------- Drive upload ----------


@dg.asset(
    key=["google_drive", "uploaded_snapshots"],
    group_name="backup",
    compute_kind="googledrive",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    deps=[dg.AssetDep(["google_drive", "storage_capacity"])],
    check_specs=[
        dg.AssetCheckSpec(
            name="all_snapshots_uploaded",
            asset=dg.AssetKey(["google_drive", "uploaded_snapshots"]),
            blocking=True,
            description="Drive partition dir contains exactly the expected snapshot files.",
        )
    ],
    description="Copy the partition's snapshot dir to the Drive remote.",
)
def upload_snapshots_to_drive(
    context: dg.AssetExecutionContext,
    backup: BackupResource,
    rclone: RcloneResource,
):
    partition = context.partition_key
    src = backup.get_partition_dir(partition)
    dst = rclone.remote_path(rclone.drive_root, partition)
    started = datetime.now(tz=UTC)
    subprocess.run(
        ["rclone", "copy", str(src), dst, "--stats-one-line", "-v"],
        check=True,
    )
    duration = (datetime.now(tz=UTC) - started).total_seconds()

    local_files = sorted(p for p in src.iterdir() if p.is_file())
    total_mb = sum(p.stat().st_size for p in local_files) / (1024 * 1024)

    listed = subprocess.run(
        ["rclone", "lsjson", dst, "--files-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    remote_entries = json.loads(listed.stdout) if listed.stdout.strip() else []
    remote_names = {e["Name"] for e in remote_entries}
    expected_names = set(backup.expected_files)

    yield dg.MaterializeResult(
        metadata={
            "remote_path": dg.MetadataValue.text(dst),
            "files_uploaded": dg.MetadataValue.int(len(remote_names)),
            "mb_uploaded": dg.MetadataValue.float(total_mb),
            "duration_s": dg.MetadataValue.float(duration),
        }
    )

    missing = expected_names - remote_names
    extra = remote_names - expected_names
    passed = not missing
    yield dg.AssetCheckResult(
        check_name="all_snapshots_uploaded",
        passed=passed,
        severity=dg.AssetCheckSeverity.ERROR,
        description=(
            None if passed else f"Missing on Drive: {sorted(missing)}; extra: {sorted(extra)}."
        ),
        metadata={
            "expected": dg.MetadataValue.json(sorted(expected_names)),
            "uploaded": dg.MetadataValue.json(sorted(remote_names)),
            "missing": dg.MetadataValue.json(sorted(missing)),
            "extra": dg.MetadataValue.json(sorted(extra)),
        },
    )


# ---------- prune (parallel siblings of upload's downstream) ----------


@dg.asset(
    key=["google_drive", "pruned_old_backups"],
    group_name="backup",
    compute_kind="googledrive",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    deps=[dg.AssetDep(["google_drive", "uploaded_snapshots"])],
    description=f"Delete Drive partition dirs beyond the newest {MAX_DRIVE_BACKUPS}.",
)
def prune_drive_backups(
    context: dg.AssetExecutionContext, rclone: RcloneResource
) -> dg.MaterializeResult:
    root = rclone.remote_path(rclone.drive_root)
    listed = subprocess.run(
        ["rclone", "lsjson", root, "--dirs-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    entries = json.loads(listed.stdout) if listed.stdout.strip() else []
    dir_names = sorted(e["Name"] for e in entries if e.get("IsDir"))

    to_delete = dir_names[:-MAX_DRIVE_BACKUPS] if len(dir_names) > MAX_DRIVE_BACKUPS else []
    for name in to_delete:
        subprocess.run(["rclone", "purge", rclone.remote_path(rclone.drive_root, name)], check=True)
        context.log.info("Drive: purged %s", name)

    summary = (
        f"**Drive retention** — kept {len(dir_names) - len(to_delete)} / "
        f"deleted {len(to_delete)} (target ≤ {MAX_DRIVE_BACKUPS})\n\n"
        + ("\n".join(f"- `{n}`" for n in to_delete) if to_delete else "_nothing to prune_")
    )

    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(summary),
            "deleted": dg.MetadataValue.json(to_delete),
            "deleted_count": dg.MetadataValue.int(len(to_delete)),
            "kept_count": dg.MetadataValue.int(len(dir_names) - len(to_delete)),
            "retention_n": dg.MetadataValue.int(MAX_DRIVE_BACKUPS),
        }
    )


@dg.asset(
    key=["local_disk", "pruned_old_backups"],
    group_name="backup",
    compute_kind="file",
    code_version=BACKUP_READINGS_DAG_VERSION,
    partitions_def=daily_partition_def,
    deps=[dg.AssetDep(["google_drive", "uploaded_snapshots"])],
    description=f"Delete local partition dirs beyond the newest {MAX_LOCAL_BACKUPS}.",
)
def prune_local_backups(
    context: dg.AssetExecutionContext, backup: BackupResource
) -> dg.MaterializeResult:
    backup_root = backup.get_backup_dir()
    if not backup_root.exists():
        return dg.MaterializeResult(metadata={"summary": dg.MetadataValue.md("_no backup root_")})

    dirs = sorted(d for d in backup_root.iterdir() if d.is_dir())
    to_delete = dirs[:-MAX_LOCAL_BACKUPS] if len(dirs) > MAX_LOCAL_BACKUPS else []
    for d in to_delete:
        shutil.rmtree(d)
        context.log.info("Local: removed %s", d.name)

    deleted_names = [d.name for d in to_delete]
    summary = (
        f"**Local retention** — kept {len(dirs) - len(to_delete)} / "
        f"deleted {len(to_delete)} (target ≤ {MAX_LOCAL_BACKUPS})\n\n"
        + ("\n".join(f"- `{n}`" for n in deleted_names) if deleted_names else "_nothing to prune_")
    )

    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(summary),
            "deleted": dg.MetadataValue.json(deleted_names),
            "deleted_count": dg.MetadataValue.int(len(to_delete)),
            "kept_count": dg.MetadataValue.int(len(dirs) - len(to_delete)),
            "retention_n": dg.MetadataValue.int(MAX_LOCAL_BACKUPS),
        }
    )


# Ordered list for explicit selection by jobs/tests.
# `ping_healthcheck` is NOT here — it's a run-status sensor (see sensors.py),
# fired after the job succeeds rather than as a per-partition asset, since the
# ping itself is ephemeral (no per-day metadata worth scrolling back to).
all_assets = [
    snapshot_raw_store,
    snapshot_sessions,
    snapshot_notes,
    snapshot_research,
    storage_capacity,
    upload_snapshots_to_drive,
    prune_drive_backups,
    prune_local_backups,
]
