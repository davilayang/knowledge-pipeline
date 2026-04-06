import dagster as dg

from .assets import chunked_contents, embedded_contents, indexed_contents, raw_store_copy

index_contents_job = dg.define_asset_job(
    name="rag_0_baseline",
    selection=[raw_store_copy, chunked_contents, embedded_contents, indexed_contents],
    description="Baseline index: markdown chunking + MiniLM embedding",
    config={
        "execution": {
            "config": {
                "multiprocess": {
                    "max_concurrent": 3,
                },
            },
        },
    },
)

daily_index_schedule = dg.ScheduleDefinition(
    job=index_contents_job,
    cron_schedule="0 7 * * *",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

defs = dg.Definitions(
    assets=[raw_store_copy, chunked_contents, embedded_contents, indexed_contents],
    jobs=[index_contents_job],
    schedules=[daily_index_schedule],
)
