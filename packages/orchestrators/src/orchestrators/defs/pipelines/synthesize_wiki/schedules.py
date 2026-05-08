# Asset job + daily schedule for synthesize_wiki.
#
# Schedule fires at 06:00 UTC and emits a bare RunRequest — no run_config.
# Discovery of pending items lives in wiki/pending; the schedule is reduced
# to a freshness guard so a missing/stale snapshot becomes a SkipReason
# (cheap, no run created) rather than a daily-failing materialization until
# backup_readings catches up.

from datetime import date

import dagster as dg

from .assets import all_assets
from .def_config import (
    JOB_MAX_RETRIES,
    MAX_SNAPSHOT_AGE_DAYS,
    PIPELINE_TAG,
    SCHEDULE_CRON,
)
from .resources import WikiResource

synthesize_wiki_job = dg.define_asset_job(
    name="synthesize_wiki",
    description=(
        "Daily LLM synthesis of pending raw_store items into structured "
        "wiki pages (concept/tool/trend) backed by Postgres, then "
        "regenerate the wiki index."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={
        "project": PIPELINE_TAG,
        "dagster/max_retries": JOB_MAX_RETRIES,
    },
)


@dg.schedule(
    cron_schedule=SCHEDULE_CRON,
    job=synthesize_wiki_job,
    required_resource_keys={"wiki"},
)
def run_daily_synthesize_wiki(
    context: dg.ScheduleEvaluationContext,
) -> dg.RunRequest | dg.SkipReason:
    wiki: WikiResource = context.resources.wiki
    snapshot = wiki.latest_raw_store_snapshot()
    if snapshot is None:
        return dg.SkipReason(f"no raw_store snapshot under {wiki.backup_dir}")
    _, snapshot_date = snapshot
    age_days = (date.today() - snapshot_date).days
    if age_days > MAX_SNAPSHOT_AGE_DAYS:
        return dg.SkipReason(
            f"newest raw_store snapshot is {snapshot_date.isoformat()} "
            f"({age_days} days old, limit {MAX_SNAPSHOT_AGE_DAYS})"
        )

    partition = context.scheduled_execution_time.date().isoformat()
    return dg.RunRequest(run_key=partition, partition_key=partition)


__all__ = ["synthesize_wiki_job", "run_daily_synthesize_wiki"]
