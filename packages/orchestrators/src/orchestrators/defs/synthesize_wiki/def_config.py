# Definition-time config for the synthesize_wiki pipeline. Static path config
# (DATA_DIR) lives in orchestrators.config; per-host paths (BACKUP_DST_DIR,
# DATABASE_URL) are required dg.EnvVar fields on WikiResource.

import dagster as dg

# ---------- partitioning ----------

# Daily partition aligned with snapshots/raw_store (both default end_offset=0):
# partition_key = data-date. wiki/pending(D) reads snapshot(D) via the default
# IdentityPartitionMapping. Schedule on day D fires partition D-1, after backup
# materialised it at 03:00 UTC.
wiki_daily_partition_def = dg.DailyPartitionsDefinition(start_date="2026-05-01")


# ---------- cost guardrail ----------

# Cap on items processed per scheduled tick. Limits per-run LLM spend and
# OpenAI rate-limit pressure; wiki/pending slices `eligible[:WIKI_MAX_PER_TICK]`.
# 0 = no cap.
WIKI_MAX_PER_TICK = 30


SOURCE_RAW_STORE = "raw_store"


# ---------- raw_store content-id allowlist ----------

# raw_store.contents.content_id is shaped like "<origin>::<url>" (e.g.
# "medium::https://medium.com/..."). This tuple gates which origins flow
# through wiki synthesis. Today: article-shape only — current prompts assume
# article inputs (single-author, narrative, markdown-structured); transcripts
# (podcast/video) need separate prompt handling + chunking before they can be
# safely synthesised. Extend after the eval harness lands and per-source
# prompt sets exist.
ALLOWED_CONTENT_ID_PREFIXES: tuple[str, ...] = ("medium::",)


# ---------- entity rejection list (denylist) ----------

# Entity_ids to never build or update a wiki page for (W2.5). The deterministic
# denylist sibling of ALLOWED_CONTENT_ID_PREFIXES: any extracted entity_id in
# this set is dropped at extraction time, so synthesis never spends an LLM call
# on it and no page is written. Curator-owned; exact-match on the LLM-minted
# {page_type}__{slug} id. This checked-in set is the v1 source behind the seam —
# swappable for a DB/Notion-backed list later without touching the filter.
REJECTED_ENTITY_IDS: frozenset[str] = frozenset()


# ---------- schedule ----------

SCHEDULE_CRON = "0 6 * * *"


# ---------- job tags ----------

# Single source of truth for the run-group tag on this pipeline. Used as both
# the job's "project" tag (UI filtering) and the per-asset op concurrency_key
# (throttle parallel synthesis runs sharing OpenAI quota / PG connections).
PIPELINE_TAG = "synthesize-wiki"

JOB_MAX_RETRIES = "1"
