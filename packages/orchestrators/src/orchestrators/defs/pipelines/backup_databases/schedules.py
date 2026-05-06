# Backup job + daily schedule.
#
# Schedule fires at 03:00 UTC for the previous day's partition. run_key is the
# partition date so accidental double-fires are deduped by Dagster.

from datetime import timedelta

import dagster as dg

from .assets import all_assets
from .checks import all_checks
from .partitions import daily_partition_def

backup_databases_job = dg.define_asset_job(
    name="backup_databases",
    selection=dg.AssetSelection.assets(*all_assets),
    partitions_def=daily_partition_def,
    tags={
        "project": "newsletter-backup",
        "dagster/max_retries": "1",
    },
)


@dg.schedule(cron_schedule="0 3 * * *", job=backup_databases_job)
def run_daily_backup(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    yesterday = (context.scheduled_execution_time.date() - timedelta(days=1)).isoformat()
    return dg.RunRequest(run_key=yesterday, partition_key=yesterday)


__all__ = ["backup_databases_job", "run_daily_backup", "all_checks"]
