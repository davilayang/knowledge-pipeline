# Definition-time config for the populate_vector_store pipeline.

import os

import dagster as dg

# Hourly partition; schedule emits the current-hour key. start_date set to
# pipeline land date so backfills don't accidentally enumerate empty history.
vector_store_hourly_partition_def = dg.HourlyPartitionsDefinition(
    start_date="2026-05-11-00:00",
    end_offset=1,
)

SCHEDULE_CRON = "*/30 * * * *"

MAX_PER_TICK_DEFAULT = int(os.getenv("VECTOR_STORE_MAX_PER_TICK", "50"))

EMBEDDING_MODEL_DEFAULT = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMS_DEFAULT = int(os.getenv("OPENAI_EMBEDDING_DIMS", "1536"))

COLLECTION_CONTENTS = "contents"
COLLECTION_CONVERSATIONS = "conversations"
COLLECTION_NOTES = "notes"
COLLECTION_RESEARCH = "research_documents"

CHUNKER_BY_SOURCE = {
    "raw_store": "markdown",
    "notes": "markdown",
    "sessions": "turn_grouping",
    "research": "markdown",
}
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

PIPELINE_TAG = "populate-vector-store"
