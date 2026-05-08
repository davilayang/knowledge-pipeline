# Definition-time config for the synthesize_wiki pipeline. Path-level config
# (DATA_DIR, BACKUP_DIR) lives in orchestrators.config.

import dagster as dg

# ---------- partitioning ----------

# One Dagster run per scheduled tick. Items pending in raw_store ∖
# wiki.processed travel as run_config (not partition keys), so the partition
# dimension stays bounded — wiki/index can declare deps on wiki/synthesized
# with IdentityPartitionMapping. Start date is the day this shape lands.
wiki_daily_partition_def = dg.DailyPartitionsDefinition(start_date="2026-05-01", end_offset=1)


# ---------- cost guardrail ----------

# Default cap on items processed per scheduled tick. Limits per-run LLM spend
# and OpenAI rate-limit pressure; the schedule slices `pending[:max_per_tick]`.
# 0 = no cap. Tune downward if quotas tighten.
MAX_PER_TICK_DEFAULT = 30


# ---------- snapshot freshness ----------

# Skip the run if the newest backup_readings snapshot is older than this.
# Backup pipeline runs ~03:00 UTC, this schedule runs ~06:00 UTC, so a fresh
# (today) snapshot is the normal case; a 2-day window covers a single
# missed/late backup without auto-running on stale data.
MAX_SNAPSHOT_AGE_DAYS = 2


# ---------- intra-op concurrency ----------

# Cap on concurrent per-item synthesis calls inside a single run. The asset
# fans out N items via a ThreadPoolExecutor; this is the executor's max_workers.
# Sized to the OpenAI rate-limit headroom we observed during the styleguide
# rewrite — ~5 parallel mini calls is the sweet spot.
SYNTHESIS_CONCURRENCY = 5


# Source vocabulary for IngestItem.source_type. wiki.processed.source_type uses
# the same string. Today only raw_store is wired; sessions/local_file land
# alongside per-source discovery in a follow-up.
SOURCE_RAW_STORE = "raw_store"
SOURCE_LOCAL_FILE = "local_file"  # future
SOURCE_SESSION = "session"  # future


# ---------- schedule ----------

SCHEDULE_CRON = "0 6 * * *"


# ---------- job tags ----------

# Single source of truth for the run-group tag on this pipeline. Used as both
# the job's "project" tag (UI filtering) and the per-asset op concurrency_key
# (throttle parallel synthesis runs sharing OpenAI quota / PG connections).
PIPELINE_TAG = "synthesize-wiki"

JOB_MAX_RETRIES = "1"
