# Asset job + 30-min schedule for populate_vector_store. Lands paused
# (default_status=STOPPED) — Phase G turns it on.

import dagster as dg

from .assets import all_assets
from .def_config import PARTITION_FMT, PIPELINE_TAG, SCHEDULE_CRON

populate_vector_store_job = dg.define_asset_job(
    name="populate_vector_store",
    description=(
        "Embed pending items from each source into ChromaDB collections "
        "(contents, conversations, notes)."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={"project": PIPELINE_TAG},
)


@dg.schedule(
    cron_schedule=SCHEDULE_CRON,
    job=populate_vector_store_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
def run_populate_vector_store(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    partition = context.scheduled_execution_time.strftime(PARTITION_FMT)
    return dg.RunRequest(run_key=partition, partition_key=partition)


__all__ = ["populate_vector_store_job", "run_populate_vector_store"]
