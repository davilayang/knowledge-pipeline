# `synthesize_wiki` runbook

LangGraph-driven synthesis of `raw_store` items into a structured wiki
(concepts, tools, trends) backed by Postgres. One scheduled tick per day
(06:00 UTC) = one Dagster run = full pending → synthesized → index cycle.

## DAG (per scheduled tick)

Failure cascade — what blocks what when a step fails:

```
schedule run_daily_synthesize_wiki   (cron 0 6 * * *)
  │  reads raw_store IDs ∖ wiki.processed at fire time
  │  → SkipReason if empty; else one RunRequest with item_ids in run_config
  ▼
wiki/synthesized   (key: wiki/synthesized — daily partition)
  │  loops config.item_ids through invoke_wiki_synthesis (ThreadPoolExecutor,
  │  cap = SYNTHESIS_CONCURRENCY)
  │
  │     extract_entities ─→ Send-fan-out: process_entity (×N) ─→ commit
  │     pages + aliases + wiki.processed all written in ONE PG transaction
  │     per item. Aliases use ON CONFLICT DO NOTHING for cross-item safety.
  │
  │  ↻ retry on the same date partition replays the same item_ids; per-item
  │    LangGraph checkpoints skip already-completed nodes (no duplicate
  │    LLM spend if the prior failure was infra-side).
  ▼
wiki/index   (key: wiki/index — daily partition; deps wiki/synthesized)
  reads wiki.pages → writes data/wiki/index.md (table of contents)
```

- **Schedule fails** (raw_store path missing, PG unreachable) → no run
  materialized; the next cron tick retries from scratch.
- **`wiki/synthesized` per-item LLM failures** → swallowed into
  `wiki.processed` with `status='error'`; the run continues other items.
  The Dagster run shows green.
- **`wiki/synthesized` run-level failures** (an item raises out of the
  workflow — auth, infra) → the asset raises `dg.Failure`, the run fails,
  Dagster retry replays the same `item_ids`. Per-item checkpoints prevent
  duplicate LLM spend.
- **`wiki/index` fails** → today's partition for `wiki/index` stays
  unmaterialized; `wiki.pages` remains authoritative; re-materialize the
  index manually or wait for the next tick.

## Operations

### Daily run (default)

The schedule fires at 06:00 UTC. Nothing to do — runs land in the Runs page
under job `synthesize_wiki`, one per day.

### Manual run (backfill, ad-hoc)

UI: Jobs → `synthesize_wiki` → Launchpad → pick partition (date) →
Materialize. The Launchpad's run config form requires `item_ids`; either
fill in IDs manually or paste the schedule's most recent `item_ids`.

CLI:

```bash
dg launch --job synthesize_wiki -m orchestrators.defs.pipelines.definitions \
  --partition $(date +%Y-%m-%d) \
  --config '{"ops": {"wiki__synthesized": {"config": {"item_ids": ["abc123","def456"]}}}}'
```

The op name is `wiki__synthesized` (asset key joined with `__`), not the
function name.

### Re-process a single item from scratch

A retry auto-resumes from the LangGraph checkpoint. To force a clean re-run
(e.g. you changed prompts and want fresh LLM output):

```sql
-- Drop the processed marker so the next schedule tick picks it up again.
DELETE FROM wiki.processed WHERE item_id = '<item_id>';

-- (Optional) drop the LangGraph checkpoint so it doesn't auto-resume.
DELETE FROM checkpoints       WHERE thread_id = 'wiki_synthesis__<item_id>';
DELETE FROM checkpoint_writes WHERE thread_id = 'wiki_synthesis__<item_id>';
DELETE FROM checkpoint_blobs  WHERE thread_id = 'wiki_synthesis__<item_id>';
```

### Adjust cadence

Edit `SCHEDULE_CRON` in `def_config.py`. Examples:

- `"0 6 * * *"` — daily 06:00 UTC (current)
- `"0 */4 * * *"` — every 4 hours; same daily partition reused for ticks
  that fall on the same UTC day
- `"0 * * * *"` — hourly; consider switching `wiki_daily_partition_def`
  to `dg.HourlyPartitionsDefinition` to avoid catalog noise (existing
  daily partitions are orphaned per the rebuild-don't-migrate doctrine)

### LLM rate limit / quota

The asset fans out at most `SYNTHESIS_CONCURRENCY` (5) concurrent items.
Each item internally fans out per extracted entity via the LangGraph
Send API. If you're hitting OpenAI 429s:

- Lower `SYNTHESIS_CONCURRENCY` in `def_config.py`.
- Lower `MAX_PER_TICK_DEFAULT` in `def_config.py` so fewer items queue
  per tick.
- Wait — the runner doesn't catch 429; the run fails, you retry, the
  per-item checkpointer resumes from where it stopped.

### TOC missing or stale

Re-materialize `wiki/index` for today's partition. It reads everything in
`wiki.pages` and overwrites `data/wiki/index.md` from scratch — idempotent.

### Inspecting state

```sql
-- What's been processed, with status.
SELECT status, COUNT(*) FROM wiki.processed GROUP BY status;

-- Failed items (workflow caught error, committed an error marker).
SELECT item_id, error FROM wiki.processed WHERE status = 'error';

-- Pages by type.
SELECT page_type, COUNT(*) FROM wiki.pages GROUP BY page_type;
```

A row with `status='error'` is a *successful* asset run from Dagster's
perspective — the workflow handled the failure and recorded it. Distinct
from a Dagster run failure (auth error, PG unreachable, etc.).

## External setup

### LLM API key

The workflow uses two OpenAI models (configurable in
`packages/workflows/src/workflows/wiki_synthesis/`):

- `gpt-4.1-nano` — entity extraction (`nodes.py:EXTRACTION_MODEL`)
- `gpt-4.1-mini` — page synthesis (`entity_graph.py:SYNTHESIS_MODEL`)

Set `OPENAI_API_KEY` in the server's `.env`. No fallback — an unset key
fails at the first `generate(...)` call inside the workflow.

### Langfuse tracing (optional)

When `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` are
set, every LangGraph run is traced. Each item appears as one trace named
`wiki_synthesis__<item_id>` with sub-spans for `extract_entities`, each
`process_entity`, and `commit`. The `LANGFUSE_TRACING_ENVIRONMENT` env
var (e.g. `production`) tags traces for filtering across deployments.

If the Langfuse env vars are unset, the workflow runs fine without
tracing — no errors, no warnings.

### Postgres

`DATABASE_URL` points at the `knowledge_pipeline` database. The compose
postgres container auto-creates that DB and applies the wiki schema on
first start (see `docker/postgres/init/`). For non-compose deploys,
apply manually once:

```bash
psql -d knowledge_pipeline -f packages/domains/src/domains/wiki/schema/wiki.sql
```

Schema CHANGE: `docker compose down -v && docker compose up -d postgres`
re-runs the init scripts on a fresh volume (per the rebuild-don't-migrate
decision). The same Postgres instance hosts LangGraph checkpoints
(separate tables managed by `langgraph-checkpoint-postgres`); no extra
setup beyond the URL.
