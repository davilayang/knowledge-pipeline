# Definition-time config for the synthesize_wiki pipeline. Path-level config
# (DATA_DIR, LOCAL_RAW_STORE) lives in orchestrators.config.

import dagster as dg

# ---------- partitioning ----------

# One dynamic partition per source item_id. discover_pending_content
# registers new partitions; synthesize_content materializes one per
# call, which lets Dagster fan out as many concurrent runs as the
# queue and concurrency_key permit.
items_partitions_def = dg.DynamicPartitionsDefinition(name="wiki_items")


# ---------- cost guardrail ----------

# Default cap on partitions registered per discover_pending_content run. The full backlog
# is processed in batches across multiple runs. 0 = no cap (process all
# pending at once — be sure max_concurrent_runs and OpenAI rate limits can
# absorb the resulting fan-out).
MAX_ARTICLES_DEFAULT = 50


# ---------- job tags ----------

# Single source of truth for the run-group tag on this pipeline. Used as both
# the job's "project" tag (UI filtering) and the per-asset op concurrency_key
# (throttle parallel synthesis runs sharing OpenAI quota / PG connections).
PIPELINE_TAG = "synthesize-wiki"

JOB_MAX_RETRIES = "1"
