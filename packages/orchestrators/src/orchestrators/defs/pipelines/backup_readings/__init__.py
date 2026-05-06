# Backup pipeline — daily-partitioned snapshot of newsletter-assistant SQLite
# DBs with optional Drive offload (rclone) and healthchecks.io success ping.
#
# Env vars (all optional except source path on non-default hosts):
#   BACKUP_SOURCE_DIR     — defaults to ~/newsletter-assistant/data
#   BACKUP_DIR            — defaults to <repo>/backups
#   DRIVE_REMOTE          — rclone remote name; unset disables Drive flow
#   HEALTHCHECK_PING_URL  — full ping URL; unset disables success ping

import dagster as dg
from dagster._core.definitions.metadata import (
    AnchorBasedFilePathMapping,
    link_code_references_to_git,
    with_source_code_references,
)

from orchestrators.config import PROJECT_DIR

from .assets import all_assets
from .checks import all_checks
from .resources import build_resources
from .schedules import backup_readings_job, run_daily_backup
from .sensors import all_sensors

# Click-through from any asset in the Dagster UI to the source line on GitHub.
_REPO_URL = "https://github.com/davilayang/knowledge-pipeline"
_assets_with_source_links = link_code_references_to_git(
    with_source_code_references(all_assets),
    git_url=_REPO_URL,
    git_branch="main",
    file_path_mapping=AnchorBasedFilePathMapping(
        local_file_anchor=PROJECT_DIR,
        file_anchor_path_in_repository=".",
    ),
)

defs = dg.Definitions(
    assets=_assets_with_source_links,
    asset_checks=all_checks,
    jobs=[backup_readings_job],
    schedules=[run_daily_backup],
    sensors=all_sensors,
    resources=build_resources(),
)
