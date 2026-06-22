# Asset job + daily schedule for synthesize_wiki.
#
# Schedule fires at 06:00 UTC and emits a bare RunRequest for partition D-1
# (the data-date that backup_readings materialised at 03:00 UTC the same day).
# wiki/pending validates the snapshot exists and raises if missing — no
# freshness window, no fallback, deliberately fail loud so the operator
# notices a stalled backup.

from datetime import timedelta

import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG, SCHEDULE_CRON

synthesize_wiki_job = dg.define_asset_job(
    name="synthesize_wiki",
    description=(
        "Daily LLM synthesis of pending raw_store items into structured "
        "wiki pages (open-domain entity types) backed by wiki.db, then "
        "regenerate the wiki index."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={
        "project": PIPELINE_TAG,
        "dagster/max_retries": JOB_MAX_RETRIES,
    },
)


@dg.schedule(cron_schedule=SCHEDULE_CRON, job=synthesize_wiki_job)
def run_daily_synthesize_wiki(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    # Partition key = data-date (matches snapshots/raw_store). On day D fire
    # partition D-1 — the snapshot backup_readings produced at 03:00 UTC.
    partition = (context.scheduled_execution_time.date() - timedelta(days=1)).isoformat()
    return dg.RunRequest(run_key=partition, partition_key=partition)


__all__ = ["synthesize_wiki_job", "run_daily_synthesize_wiki"]
