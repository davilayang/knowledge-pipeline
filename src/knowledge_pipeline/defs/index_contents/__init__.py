import dagster as dg

from .assets import indexed_contents, no_indexing_errors, pending_contents, raw_store_copy
from .resources import RawStoreResource, VectorStoreResource

index_contents_job = dg.define_asset_job(
    name="index_contents_job",
    selection=[raw_store_copy, pending_contents, indexed_contents],
    description="Copy raw_store.db, then chunk and index pending content into ChromaDB",
)

daily_index_schedule = dg.ScheduleDefinition(
    job=index_contents_job,
    cron_schedule="0 7 * * *",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

defs = dg.Definitions(
    assets=[raw_store_copy, pending_contents, indexed_contents],
    asset_checks=[no_indexing_errors],
    jobs=[index_contents_job],
    schedules=[daily_index_schedule],
    resources={
        "raw_store": RawStoreResource(),
        "vector_store": VectorStoreResource(),
    },
)
