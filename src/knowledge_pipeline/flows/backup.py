# Prefect flow: Backup SQLite databases from newsletter-assistant.

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, task
from prefect.artifacts import create_markdown_artifact

from knowledge_pipeline.config import BACKUP_DIR, DB_FILES, MAX_BACKUPS, SOURCE_DATA_DIR

logger = logging.getLogger(__name__)


@task(
    name="backup-database",
    description="Copy a single database file to the backup directory with timestamp",
    retries=1,
    retry_delay_seconds=5,
)
def backup_database(db_name: str, backup_subdir: Path) -> dict:
    """Copy a database file to the timestamped backup directory.

    Returns a dict with backup details.
    """
    source = SOURCE_DATA_DIR / db_name
    if not source.exists():
        logger.warning("Source database not found, skipping: %s", source)
        return {"name": db_name, "status": "not_found", "size_bytes": 0}

    backup_subdir.mkdir(parents=True, exist_ok=True)
    dest = backup_subdir / db_name
    shutil.copy2(source, dest)

    # Also copy WAL and SHM files if they exist (for consistency)
    for suffix in ["-wal", "-shm"]:
        wal_src = source.parent / f"{db_name}{suffix}"
        if wal_src.exists():
            shutil.copy2(wal_src, backup_subdir / f"{db_name}{suffix}")

    size = dest.stat().st_size
    logger.info("Backed up %s (%d bytes) → %s", db_name, size, dest)
    return {"name": db_name, "status": "ok", "size_bytes": size}


@task(
    name="cleanup-old-backups",
    description="Remove old backup directories beyond the retention limit",
)
def cleanup_old_backups(max_backups: int = MAX_BACKUPS) -> int:
    """Remove oldest backup directories, keeping only max_backups most recent.

    Returns the number of directories removed.
    """
    if not BACKUP_DIR.exists():
        return 0

    # Backup dirs are named like 2026-03-14T09-30-00Z — lexicographic sort works
    subdirs = sorted(
        [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    removed = 0
    while len(subdirs) > max_backups:
        oldest = subdirs.pop(0)
        shutil.rmtree(oldest)
        logger.info("Removed old backup: %s", oldest.name)
        removed += 1

    return removed


@task(
    name="create-backup-report",
    description="Create a Prefect artifact summarising the backup run",
)
def create_backup_report(results: list[dict], removed: int, backup_dir_name: str) -> dict:
    backed_up = [r for r in results if r["status"] == "ok"]
    not_found = [r for r in results if r["status"] == "not_found"]
    total_bytes = sum(r["size_bytes"] for r in backed_up)

    def _fmt_size(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    lines = [
        f"**Backup directory:** `backups/{backup_dir_name}`",
        f"**Files backed up:** {len(backed_up)} ({_fmt_size(total_bytes)})",
    ]

    if backed_up:
        lines.append("\n| File | Size |")
        lines.append("| --- | --- |")
        for r in backed_up:
            lines.append(f"| `{r['name']}` | {_fmt_size(r['size_bytes'])} |")

    if not_found:
        lines.append(f"\n**Not found:** {', '.join(r['name'] for r in not_found)}")

    if removed > 0:
        lines.append(f"\n**Old backups removed:** {removed}")

    create_markdown_artifact(
        key="backup-summary",
        markdown="\n".join(lines),
        description="Database backup run summary",
    )

    summary = {
        "backed_up": len(backed_up),
        "not_found": len(not_found),
        "total_bytes": total_bytes,
        "old_removed": removed,
    }
    logger.info("Backup report: %s", summary)
    return summary


# ── Flow ─────────────────────────────────────────────────────────────────────


@flow(
    name="backup-databases",
    description=(
        "Back up SQLite databases from newsletter-assistant to timestamped "
        "backup directories, with automatic retention cleanup."
    ),
    retries=0,
)
def backup_databases(
    max_backups: int = MAX_BACKUPS,
    db_files: list[str] | None = None,
) -> dict:
    """Main flow: copy DBs → cleanup old → report.

    Args:
        max_backups: Number of backup directories to retain.
        db_files: List of database filenames to back up (default: all configured).
    """
    if db_files is None:
        db_files = DB_FILES

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_subdir = BACKUP_DIR / timestamp

    results: list[dict] = []
    for db_name in db_files:
        result = backup_database(db_name, backup_subdir)
        results.append(result)

    removed = cleanup_old_backups(max_backups=max_backups)

    return create_backup_report(results, removed, timestamp)
