# Definition-time config for the synthesize_wiki pipeline. Path-level config
# (DATA_DIR, LOCAL_RAW_STORE) lives in orchestrators.config.

import dagster as dg

# ---------- partitioning ----------

# One dynamic partition per IngestItem (source-prefixed item_id). The
# per-source discover_pending_* assets each register partitions here;
# synthesize_item materializes one per call regardless of source. This
# lets Dagster fan out as many concurrent runs as the queue and
# concurrency_key permit.
item_partitions_def = dg.DynamicPartitionsDefinition(name="wiki_items")


# ---------- cost guardrail ----------

# Default cap on partitions registered per discover_pending_* run. The full backlog
# is processed in batches across multiple runs. 0 = no cap (process all
# pending at once — be sure max_concurrent_runs and OpenAI rate limits can
# absorb the resulting fan-out).
MAX_ARTICLES_DEFAULT = 30


# ---------- job tags ----------

# Single source of truth for the run-group tag on this pipeline. Used as both
# the job's "project" tag (UI filtering) and the per-asset op concurrency_key
# (throttle parallel synthesis runs sharing OpenAI quota / PG connections).
PIPELINE_TAG = "synthesize-wiki"

JOB_MAX_RETRIES = "1"
