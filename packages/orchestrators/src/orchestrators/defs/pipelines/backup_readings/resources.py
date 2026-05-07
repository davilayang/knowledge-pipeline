# Dagster resources for the backup pipeline.
#
# Required env vars (DRIVE_REMOTE, HEALTHCHECK_PING_URL) use dg.EnvVar so they
# resolve at run-init, not at definitions load. Result: the gRPC server still
# loads on a laptop without these set; only a run that actually uses the Drive
# or healthcheck resource will fail fast at startup. Laptop dev runs the
# snapshot subset (see README) and never touches those resources.

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
    """rclone remote for Drive upload + retention."""

    remote_name: str

    def remote_path(self, *parts: str) -> str:
        # rclone path syntax: "<remote>:<path>"
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{self.remote_name}:{joined}"


class HealthcheckResource(dg.ConfigurableResource):
    """healthchecks.io ping URL."""

    ping_url: str


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "backup": BackupResource(),
        "rclone": RcloneResource(remote_name=dg.EnvVar("DRIVE_REMOTE")),
        "healthcheck": HealthcheckResource(ping_url=dg.EnvVar("HEALTHCHECK_PING_URL")),
    }
