# Dagster resources for the backup pipeline.

import os
from pathlib import Path

import dagster as dg

from orchestrators.config import BACKUP_DIR, BACKUP_SOURCE_DIR, DB_FILES


class BackupResource(dg.ConfigurableResource):
    """Where to read source DBs from and where to land local snapshots."""

    source_data_dir: str = str(BACKUP_SOURCE_DIR)
    backup_dir: str = str(BACKUP_DIR)
    db_files: list[str] = DB_FILES

    def get_source_dir(self) -> Path:
        return Path(self.source_data_dir)

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def get_partition_dir(self, partition_key: str) -> Path:
        return self.get_backup_dir() / partition_key


class RcloneResource(dg.ConfigurableResource):
    """rclone remote for Drive upload + retention. Env-driven so the same code
    runs on a laptop without rclone configured (Drive assets short-circuit)."""

    remote_name: str = ""  # populated from DRIVE_REMOTE env at instantiation

    @property
    def is_configured(self) -> bool:
        return bool(self.remote_name)

    def remote_path(self, *parts: str) -> str:
        # rclone path syntax: "<remote>:<path>"
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{self.remote_name}:{joined}"


class HealthcheckResource(dg.ConfigurableResource):
    """healthchecks.io ping URL. Empty = no terminal ping (development mode)."""

    ping_url: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.ping_url)


def build_resources() -> dict[str, dg.ConfigurableResource]:
    # .strip() so a whitespace-only env value reads as unset (matches `is_configured`).
    return {
        "backup": BackupResource(),
        "rclone": RcloneResource(remote_name=os.getenv("DRIVE_REMOTE", "").strip()),
        "healthcheck": HealthcheckResource(
            ping_url=os.getenv("HEALTHCHECK_PING_URL", "").strip()
        ),
    }
