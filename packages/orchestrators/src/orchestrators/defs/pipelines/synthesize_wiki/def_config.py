# Definition-time config for the synthesize_wiki pipeline. Path-level config
# (DATA_DIR, LOCAL_RAW_STORE) lives in orchestrators.config.

import dagster as dg

# ---------- partitioning ----------

# One dynamic partition per IngestItem (source-prefixed item_id). The
# per-source discover_pending_* assets each register partitions here;
# synthesize_item materializes one per call regardless of source. This
# lets Dagster fan out as many concurrent runs as the queue and
# concurrency_key permit.
WIKI_ITEMS_PARTITIONS_NAME = "wiki_items"
item_partitions_def = dg.DynamicPartitionsDefinition(name=WIKI_ITEMS_PARTITIONS_NAME)


# ---------- cost guardrail ----------

# Default cap on partitions registered per discover_pending_* run. Applied
# per-source (additive across sources — three discoverers each capped at
# 30 means up to 90 new partitions per cycle). 0 = no cap. Tune
# downward if LLM rate limits or OpenAI spend become a concern.
MAX_PER_DISCOVERY_DEFAULT = 30


# Source vocabulary for IngestItem.source_type and partition-key prefixes.
# Partition keys take the form "<source>:<raw_id>" (e.g. "raw_store:abc123")
# so the wiki_items partition set can hold multiple source kinds without
# id collisions. wiki.processed.source_type uses the same string.
SOURCE_RAW_STORE = "raw_store"
SOURCE_LOCAL_FILE = "local_file"  # future
SOURCE_SESSION = "session"  # future


# ---------- job tags ----------

# Single source of truth for the run-group tag on this pipeline. Used as both
# the job's "project" tag (UI filtering) and the per-asset op concurrency_key
# (throttle parallel synthesis runs sharing OpenAI quota / PG connections).
PIPELINE_TAG = "synthesize-wiki"

JOB_MAX_RETRIES = "1"
