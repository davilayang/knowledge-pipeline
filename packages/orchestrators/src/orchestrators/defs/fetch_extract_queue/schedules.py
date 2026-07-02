import dagster as dg

from .assets import all_assets, render_attributed_pages
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG

fetch_extract_queue_job = dg.define_asset_job(
    name="fetch_extract_queue",
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    description="One run per Fetching Notion row: fetch then extract then flip lifecycle to Ready.",
    tags={"project": PIPELINE_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)

# The attributed-page render is unpartitioned (a sweep over ALL entities in
# wiki.db), so it cannot join the partitioned job above — it gets its own job.
render_attributed_pages_job = dg.define_asset_job(
    name="render_attributed_pages",
    selection=dg.AssetSelection.assets(render_attributed_pages.key),
    description="Sweep: re-render every entity's attributed wiki page from wiki.db.",
    tags={"project": PIPELINE_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)


# Persist runs per-row (sensor-driven) throughout the day; a daily sweep re-renders
# every page from the current wiki.db. 07:00, after the 06:00 wiki-write window.
@dg.schedule(cron_schedule="0 7 * * *", job=render_attributed_pages_job)
def render_attributed_pages_schedule(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    return dg.RunRequest()
