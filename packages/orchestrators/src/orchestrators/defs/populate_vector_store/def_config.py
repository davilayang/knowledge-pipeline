# Definition-time config for the populate_vector_store pipeline.

import dagster as dg

# 30-min partition; schedule emits the current half-hour key. start set to
# pipeline land date so backfills don't accidentally enumerate empty history.
SCHEDULE_CRON = "*/30 * * * *"
PARTITION_FMT = "%Y-%m-%d-%H:%M"
vector_store_partition_def = dg.TimeWindowPartitionsDefinition(
    start="2026-05-11-00:00",
    fmt=PARTITION_FMT,
    cron_schedule=SCHEDULE_CRON,
    end_offset=1,
)

MAX_PER_TICK_DEFAULT = 50

# Embedding model + dims are coupled and locked-in: changing either
# invalidates every vector in the Chroma collections. Not a deploy knob.
EMBEDDING_MODEL_DEFAULT = "text-embedding-3-small"
EMBEDDING_DIMS_DEFAULT = 1536

COLLECTION_CONTENTS = "contents"
COLLECTION_CONVERSATIONS = "conversations"
COLLECTION_NOTES = "notes"
COLLECTION_WIKI = "wiki"

CHUNKER_BY_SOURCE = {
    "raw_store": "markdown",
    "notes": "markdown",
    "sessions": "turn_grouping",
    # A one-sentence entity summary is a single chunk under any splitter, so the
    # plain recursive splitter suffices — markdown would prepend an empty heading
    # breadcrumb for a headless summary.
    "wiki": "recursive",
}
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

PIPELINE_TAG = "populate-vector-store"
