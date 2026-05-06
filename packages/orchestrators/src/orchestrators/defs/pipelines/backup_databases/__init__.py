# Backup pipeline — daily-partitioned snapshot of newsletter-assistant SQLite
# DBs with optional Drive offload (rclone) and healthchecks.io success ping.
#
# Env vars (all optional except source path on non-default hosts):
#   BACKUP_SOURCE_DIR     — defaults to ~/newsletter-assistant/data
#   BACKUP_DIR            — defaults to <repo>/backups
#   DRIVE_REMOTE          — rclone remote name; unset disables Drive flow
#   HEALTHCHECK_PING_URL  — full ping URL; unset disables success ping

import dagster as dg

from .assets import all_assets
from .checks import all_checks
from .resources import build_resources
from .schedules import backup_databases_job, run_daily_backup

defs = dg.Definitions(
    assets=all_assets,
    asset_checks=all_checks,
    jobs=[backup_databases_job],
    schedules=[run_daily_backup],
    resources=build_resources(),
)
