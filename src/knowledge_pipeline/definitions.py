# Top-level Dagster Definitions — entrypoint for `dagster dev`.

import dagster as dg

from knowledge_pipeline.defs.backup_databases.ops import backup_job
from knowledge_pipeline.defs.index_contents.assets import (
    indexed_contents,
    pending_contents,
    raw_store_copy,
)
from knowledge_pipeline.defs.index_contents.resources import RawStoreResource, VectorStoreResource

index_knowledge_job = dg.define_asset_job(
    name="index_knowledge_job",
    selection=[raw_store_copy, pending_contents, indexed_contents],
    description="Copy raw_store.db, then chunk and index pending content into ChromaDB",
)

daily_index_schedule = dg.ScheduleDefinition(
    job=index_knowledge_job,
    cron_schedule="0 7 * * *",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

defs = dg.Definitions(
    assets=[raw_store_copy, pending_contents, indexed_contents],
    jobs=[index_knowledge_job, backup_job],
    schedules=[daily_index_schedule],
    resources={
        "raw_store": RawStoreResource(),
        "vector_store": VectorStoreResource(),
    },
)
