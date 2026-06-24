# Asset job + daily schedule for sync_wiki_curation.
#
# Non-partitioned: this DAG operates on the CURRENT wiki.db / Notion state, not a
# date-partitioned snapshot. Fires at 07:00 UTC (after the 06:00 synthesis tick).
# Within the job, push_wiki_pages depends on pull_wiki_rejections, so the
# rejected set is deleted BEFORE the surviving set is pushed up.

import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, JOB_TAG, SCHEDULE_CRON

sync_wiki_curation_job = dg.define_asset_job(
    name="sync_wiki_curation",
    description=(
        "Pull curator Rejected toggles from the Notion 'Wiki Pages' DB into the "
        "local rejected_entities table (deleting rejected entities), then push "
        "the surviving wiki.db entities back up to the same DB so the curator "
        "has the latest set to review."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={
        "project": JOB_TAG,
        "dagster/max_retries": JOB_MAX_RETRIES,
    },
)


@dg.schedule(cron_schedule=SCHEDULE_CRON, job=sync_wiki_curation_job)
def run_daily_sync_wiki_curation(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    # Non-partitioned — a bare request against the live wiki.db / Notion state.
    return dg.RunRequest()


__all__ = ["sync_wiki_curation_job", "run_daily_sync_wiki_curation"]
