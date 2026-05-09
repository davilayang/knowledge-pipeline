# Dagster resources for the backup pipeline.

from pathlib import Path

import dagster as dg

from orchestrators.config import (
    ARCHIVE_FILES,
    DB_FILES,
)


class BackupResource(dg.ConfigurableResource):
    """Where to read source DBs from and where to land local snapshots."""

    source_data_dir: str
    backup_dir: str
    db_files: list[str] = DB_FILES
    archive_files: list[str] = ARCHIVE_FILES

    @property
    def expected_files(self) -> list[str]:
        return self.db_files + self.archive_files

    def get_source_dir(self) -> Path:
        return Path(self.source_data_dir)

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def get_partition_dir(self, partition_key: str) -> Path:
        return self.get_backup_dir() / partition_key


class RcloneResource(dg.ConfigurableResource):
    """rclone remote for Drive upload + retention."""

    remote_name: str
    drive_root: str

    def remote_path(self, *parts: str) -> str:
        # rclone path syntax: "<remote>:<path>"
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{self.remote_name}:{joined}"


class HealthcheckResource(dg.ConfigurableResource):
    """healthchecks.io ping URL."""

    ping_url: str


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "backup": BackupResource(
            source_data_dir=dg.EnvVar("BACKUP_SOURCE_DIR"),
            backup_dir=dg.EnvVar("BACKUP_DIR"),
        ),
        "rclone": RcloneResource(
            remote_name=dg.EnvVar("DRIVE_REMOTE"),
            drive_root=dg.EnvVar("DRIVE_BACKUP_ROOT"),
        ),
        "healthcheck": HealthcheckResource(ping_url=dg.EnvVar("HEALTHCHECK_PING_URL")),
    }
