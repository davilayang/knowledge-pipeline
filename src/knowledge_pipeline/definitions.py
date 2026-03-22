# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.

import dagster as dg

from knowledge_pipeline.defs.backup_databases.ops import backup_graph
from knowledge_pipeline.defs.index_contents.assets import (
    indexed_contents,
    no_indexing_errors,
    pending_contents,
    raw_store_copy,
)
from knowledge_pipeline.defs.index_contents.resources import RawStoreResource, VectorStoreResource

index_contents_job = dg.define_asset_job(
    name="index_contents_job",
    selection=[raw_store_copy, pending_contents, indexed_contents],
    description="Copy raw_store.db, then chunk and index pending content into ChromaDB",
)

backup_databases_job = backup_graph.to_job(
    name="backup_databases_job",
    description="Back up SQLite databases from newsletter-assistant with retention cleanup",
)

daily_index_schedule = dg.ScheduleDefinition(
    job=index_contents_job,
    cron_schedule="0 7 * * *",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

defs = dg.Definitions(
    assets=[raw_store_copy, pending_contents, indexed_contents],
    asset_checks=[no_indexing_errors],
    jobs=[index_contents_job, backup_databases_job],
    schedules=[daily_index_schedule],
    resources={
        "raw_store": RawStoreResource(),
        "vector_store": VectorStoreResource(),
    },
)
