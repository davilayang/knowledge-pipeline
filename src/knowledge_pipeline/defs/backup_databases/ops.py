# Dagster ops for database backup (side-effect work, not asset-based).
# Uses op factories to generate one op per database file at definition time.

import logging
import shutil
import sqlite3
from datetime import UTC, datetime

import dagster as dg

from knowledge_pipeline.config import DB_FILES

from .resources import BackupResource

logger = logging.getLogger(__name__)

TIMESTAMP_FMT = "%Y-%m-%dT%H-%M-%SZ"


def create_backup_op(db_name: str) -> dg.OpDefinition:
    """Factory: create a dedicated backup op for a single database file."""

    safe_name = db_name.replace(".", "_")

    # See https://docs.dagster.io/guides/build/ops
    @dg.op(
        name=f"backup_{safe_name}",
        description=f"Backup sqlite database '{db_name}' by copying to storage",
    )
    def _backup(context: dg.OpExecutionContext, backup: BackupResource) -> dict:

        timestamp = datetime.now(tz=UTC).strftime(TIMESTAMP_FMT)
        source = backup.get_source_dir() / db_name

        if not source.exists():
            logger.warning("Source database not found, skipping: %s", source)
            return {"name": db_name, "status": "not_found", "size_bytes": 0}

        backup_subdir = backup.get_backup_dir() / timestamp
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
        context.log.info("Backed up %s (%d bytes) → %s", db_name, size, dest)
        return {"name": db_name, "status": "ok", "size_bytes": size}

    return _backup


# Generate one op per configured database file
backup_ops = [create_backup_op(db_name) for db_name in DB_FILES]


@dg.op(
    name="cleanup_old_backups",
    description="Remove earlier backup directories beyond retention limit",
)
def cleanup_old_backups(
    context: dg.OpExecutionContext,
    backup: BackupResource,
    results: list[dict],
) -> dict:
    """Remove oldest backups, keeping only max_backups most recent."""
    backup_dir = backup.get_backup_dir()

    removed = 0
    if backup_dir.exists():
        subdirs = sorted(
            [d for d in backup_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )
        while len(subdirs) > backup.max_backups:
            oldest = subdirs.pop(0)
            shutil.rmtree(oldest)
            logger.info("Removed old backup: %s", oldest.name)
            removed += 1

    return {"results": results, "old_removed": removed}


@dg.op(name="log_summary", description="Log sqlite database backup summary")
def log_summary(context: dg.OpExecutionContext, final_result: dict) -> None:
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


@dg.graph()
def backup_graph():
    """Graph: one backup op per DB → collect → cleanup → log summary."""

    results = [op() for op in backup_ops]

    final = cleanup_old_backups(results=results)

    log_summary(final)
