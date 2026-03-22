# Dagster ops for database backup (side-effect work, not asset-based).

import logging
import shutil
import sqlite3
from datetime import UTC, datetime

import dagster as dg

from .resources import BackupResource

logger = logging.getLogger(__name__)


@dg.op(description="Copy database files to a timestamped backup directory")
def backup_databases(context: dg.OpExecutionContext, backup: BackupResource) -> dict:
    """Copy each configured database file to a timestamped backup directory."""
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    source_dir = backup.get_source_dir()
    backup_subdir = backup.get_backup_dir() / timestamp

    results: list[dict] = []
    for db_name in backup.db_files:
        source = source_dir / db_name
        if not source.exists():
            logger.warning("Source database not found, skipping: %s", source)
            results.append({"name": db_name, "status": "not_found", "size_bytes": 0})
            continue

        backup_subdir.mkdir(parents=True, exist_ok=True)
        dest = backup_subdir / db_name

        # Use SQLite backup API for a consistent snapshot
        src_conn = sqlite3.connect(source)
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        size = dest.stat().st_size
        logger.info("Backed up %s (%d bytes) → %s", db_name, size, dest)
        results.append({"name": db_name, "status": "ok", "size_bytes": size})

    return {"timestamp": timestamp, "results": results}


@dg.op(description="Remove old backup directories beyond retention limit")
def cleanup_old_backups(
    context: dg.OpExecutionContext, backup: BackupResource, backup_result: dict
) -> dict:
    """Remove oldest backups, keeping only max_backups most recent."""
    backup_dir = backup.get_backup_dir()
    if not backup_dir.exists():
        return {**backup_result, "old_removed": 0}

    subdirs = sorted(
        [d for d in backup_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    removed = 0
    while len(subdirs) > backup.max_backups:
        oldest = subdirs.pop(0)
        shutil.rmtree(oldest)
        logger.info("Removed old backup: %s", oldest.name)
        removed += 1

    return {**backup_result, "old_removed": removed}


@dg.op(description="Log backup summary")
def log_backup_summary(context: dg.OpExecutionContext, final_result: dict) -> None:
    """Log a summary of the backup run."""
    results = final_result["results"]
    backed_up = [r for r in results if r["status"] == "ok"]
    not_found = [r for r in results if r["status"] == "not_found"]
    total_bytes = sum(r["size_bytes"] for r in backed_up)

    def _fmt_size(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    context.log.info(
        "Backup complete: %d files (%s), %d not found, %d old removed",
        len(backed_up),
        _fmt_size(total_bytes),
        len(not_found),
        final_result["old_removed"],
    )


@dg.graph
def backup_graph():
    """Graph: backup databases → cleanup old → log summary."""
    result = backup_databases()
    final = cleanup_old_backups(backup_result=result)
    log_backup_summary(final)
