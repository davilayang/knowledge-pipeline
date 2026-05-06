# Backup job + daily schedule.
#
# Schedule fires at 03:00 UTC for the previous day's partition. run_key is the
# partition date so accidental double-fires are deduped by Dagster.

from datetime import timedelta

import dagster as dg

from .assets import all_assets
from .checks import all_checks
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG, SCHEDULE_CRON
from .partitions import daily_partition_def

backup_readings_job = dg.define_asset_job(
    name="backup_readings",
    description=(
        "Daily backup: snapshot newsletter-assistant SQLite DBs, verify integrity, "
        "offload to Google Drive (rclone), prune both sides, ping healthchecks.io. "
        "Drive + healthcheck steps short-circuit when their env vars are unset."
    ),
    selection=dg.AssetSelection.assets(*all_assets),
    partitions_def=daily_partition_def,
    tags={
        "project": PIPELINE_TAG,
        "dagster/max_retries": JOB_MAX_RETRIES,
    },
)


@dg.schedule(cron_schedule=SCHEDULE_CRON, job=backup_readings_job)
def run_daily_backup(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    yesterday = (context.scheduled_execution_time.date() - timedelta(days=1)).isoformat()
    return dg.RunRequest(run_key=yesterday, partition_key=yesterday)


__all__ = ["backup_readings_job", "run_daily_backup", "all_checks"]
