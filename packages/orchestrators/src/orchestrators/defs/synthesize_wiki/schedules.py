# Asset job + daily schedule for synthesize_wiki.
#
# Unpartitioned: the sweep operates on the CURRENT queue.db / wiki.db state, not a
# date-partitioned snapshot. Fires at 06:00 UTC (before the 07:00 curation tick).
# Within the job, render_pages depends on attribute_claims, so the sweep persists
# before any page is re-rendered.

import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, JOB_TAG, SCHEDULE_CRON

synthesize_wiki_job = dg.define_asset_job(
    name="synthesize_wiki",
    description=(
        "Sweep queue.db's stored extraction docs into wiki.db (new-or-changed "
        "sources only, by the synthesized_at watermark), then re-render every "
        "page-worthy entity to data/wiki/."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={"project": JOB_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)


@dg.schedule(cron_schedule=SCHEDULE_CRON, job=synthesize_wiki_job)
def run_daily_synthesize_wiki(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    # Unpartitioned — a bare request against the live queue.db / wiki.db state.
    return dg.RunRequest()


__all__ = ["synthesize_wiki_job", "run_daily_synthesize_wiki"]
