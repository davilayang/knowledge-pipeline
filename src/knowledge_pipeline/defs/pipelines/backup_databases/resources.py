# Dagster resource for database backup configuration.

from pathlib import Path

import dagster as dg

from knowledge_pipeline.config import BACKUP_DIR, BACKUP_SOURCE_DIR, DB_FILES, MAX_BACKUPS


class BackupResource(dg.ConfigurableResource):
    """Configuration for database backup operations."""

    source_data_dir: str = str(BACKUP_SOURCE_DIR)
    backup_dir: str = str(BACKUP_DIR)
    db_files: list[str] = DB_FILES
    max_backups: int = MAX_BACKUPS

    def get_source_dir(self) -> Path:
        return Path(self.source_data_dir)

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)
