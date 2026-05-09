# `synthesize_wiki` runbook

LangGraph-driven synthesis of `raw_store` items into a structured wiki
(concepts, tools, trends) backed by Postgres. One scheduled tick per day
(06:00 UTC) = one Dagster run = full pending → synthesized → index cycle.

## DAG (per scheduled tick)

Failure cascade — what blocks what when a step fails:

```
schedule run_daily_synthesize_wiki   (cron 0 6 * * *)
  │  fires partition (D-1) on day D — same key as backup_readings'
  │  03:00 UTC materialisation. Bare RunRequest, no run_config.
  ▼
wiki/pending   (key: wiki/pending — daily partition, key = data-date)
  │  dep: snapshots/raw_store (default IdentityPartitionMapping — same key)
  │  reads BACKUP_DIR/<partition_key>/raw_store.db; raises dg.Failure if
  │  the file is absent (backup_readings hasn't materialised that partition).
  │  Filters raw_store content_ids by ALLOWED_CONTENT_ID_PREFIXES (today:
  │  "medium::" only — current prompts assume article-shape inputs);
  │  reads eligible IDs ∖ wiki.processed; output is the capped work order
  │  (≤ MAX_PER_TICK_DEFAULT). Metadata exposes total_pending (pre-cap),
  │  queued (post-cap), capped (bool), excluded_by_source — daily backlog
  │  timeseries.
  ▼
wiki/synthesized   (key: wiki/synthesized — daily partition)
  │  in: pending (list[str] from wiki/pending via Dagster IO manager)
  │  derives the same snapshot path from its own partition_key; iterates
  │  the pending list sequentially through invoke_wiki_synthesis. No
  │  re-filter — the commit txn is idempotent (ON CONFLICT) so a retry
  │  re-processes already-committed items at the cost of duplicate LLM
  │  spend. See `assets.py` for parallelism options if throughput becomes
  │  a real constraint.
  │
  │     extract_entities ─→ Send-fan-out: process_entity (×N) ─→ commit
  │     pages + aliases + wiki.processed all written in ONE PG transaction
  │     per item. Aliases use ON CONFLICT DO NOTHING for cross-item safety.
  │
  │  ↻ retry on the same date partition replays the same pending list; per-item
  │    LangGraph checkpoints skip already-completed nodes (no duplicate
  │    LLM spend if the prior failure was infra-side).
  ▼
wiki/index   (key: wiki/index — daily partition; deps wiki/synthesized)
  reads wiki.pages → writes data/wiki/index.md (table of contents)
```

- **Snapshot missing** for the wiki partition's key (backup_readings didn't
  run, or its partition for that date hasn't materialised) → `wiki/pending`
  raises `dg.Failure` with the expected path. The schedule still fires;
  there's no fallback to an older snapshot. Fix: run `backup_readings` for
  that partition (or wait for the next 03:00 UTC tick if it'll catch up
  naturally).
- **`wiki/pending` empty list** → `wiki/synthesized` materializes a no-op
  result (`_no pending items this tick_`). Run is green; no LLM calls.
- **`wiki/synthesized` per-item LLM failures** → swallowed into
  `wiki.processed` with `status='error'`; the run continues other items.
  The Dagster run shows green.
- **`wiki/synthesized` run-level failures** (an item raises out of the
  workflow — auth, infra) → the asset raises `dg.Failure`, the run fails,
  Dagster retry replays the pickled pending list. Per-item checkpoints
  prevent duplicate LLM spend.
- **`wiki/index` fails** → today's partition for `wiki/index` stays
  unmaterialized; `wiki.pages` remains authoritative; re-materialize the
  index manually or wait for the next tick.

## Operations

### Daily run (default)

The schedule fires at 06:00 UTC. Nothing to do — runs land in the Runs page
under job `synthesize_wiki`, one per day.

### Manual run (backfill, ad-hoc)

UI: Assets → group `wiki` → select range → **Materialize**. No run config
required — `wiki/pending` discovers its own work order.

CLI:

```bash
dg launch --job synthesize_wiki -m orchestrators.defs.pipelines.definitions \
  --partition $(date +%Y-%m-%d)
```

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

Item synthesis is sequential — one item at a time within a partition's
run. Each item internally fans out per extracted entity via the
LangGraph Send API (entity-level parallelism happens inside one
`invoke_wiki_synthesis` call, not across items). If you're hitting
OpenAI 429s:

- Lower `MAX_PER_TICK_DEFAULT` in `def_config.py` so fewer items queue
  per tick.
- Wait — the runner doesn't catch 429; the run fails, you retry, the
  per-item checkpointer resumes from where it stopped.

### TOC missing or stale

Re-materialize `wiki/index` for today's partition. It reads everything in
`wiki.pages` and overwrites `data/wiki/index.md` from scratch — idempotent.

### LLM cost metadata

Each `wiki/synthesized` materialization carries `cost_usd`, `input_tokens`,
`output_tokens`, and a `cost_by_model` JSON breakdown computed from the
`PRICING_PER_1M` table in `packages/workflows/src/workflows/costs.py`.
Update that dict whenever OpenAI's pricing page changes; historical
materializations keep their point-in-time numbers.

If a model name appears in metadata's `unknown_pricing_models`, its calls
contributed 0 to the displayed cost — add it to `PRICING_PER_1M` and re-run
the partition to recompute.

Retry-cost behaviour:
- An item whose prior attempt **interrupted mid-thread** (process killed,
  PG blip during a node) → LangGraph resumes from the last checkpointed
  node on retry; only the remaining LLM calls are billed.
- An item whose prior attempt **completed successfully but the run failed
  for unrelated reasons** (sibling item raised, infra error after commit)
  → re-processed in full on retry. LLM cost re-paid; the commit txn is
  idempotent (`wiki.processed` / `wiki.pages` / `wiki.aliases` all use
  `ON CONFLICT`), so no DB conflict, but expect double-billing on the
  re-run items.

This is a deliberate trade — the alternative (re-filtering against
`wiki.processed` inside `synthesized`) reaches back into PG state that
`wiki/pending` already owns. Worst-case cost amplification is bounded
(`max_retries=1`, `MAX_PER_TICK_DEFAULT=30`).

When the run completes with `errors > 0`, the `cost_complete` metadata
boolean is `false`: per-item failures may have racked up LLM calls before
raising, and those calls aren't reflected in `cost_usd` for this
materialization. Refer to Langfuse for ground-truth per-trace cost in that
scenario.

### Inspecting state

`wiki/pending` materialization metadata is the daily backlog reading:
`total_pending` (pre-cap, eligible only), `queued` (post-cap, what got
synthesized), `capped` (bool — `true` if the queue exceeded
`MAX_PER_TICK_DEFAULT`), `excluded_by_source` (raw_store rows skipped
because their content_id prefix isn't in `ALLOWED_CONTENT_ID_PREFIXES`).
If `capped` stays `true` for more than a few days you're falling behind —
either raise the cap, increase tick frequency, or both. If
`excluded_by_source` is large you've got non-article sources accumulating
that the current prompts won't handle well — see "Adding new sources" below
before adding their prefix to the allowlist.

### Adding new sources

`ALLOWED_CONTENT_ID_PREFIXES` in `def_config.py` gates which `raw_store`
content_id prefixes flow through wiki synthesis. Today only `"medium::"`
is allowed because current prompts in
`packages/workflows/src/workflows/wiki_synthesis/prompts.py` are tuned
for article-shape inputs (single-author narrative, markdown structure).

Before adding a transcript-shape prefix (podcast, video) to the allowlist:

1. Stand up the eval harness and baseline current article quality.
2. Add a per-source-type prompt path (extraction + synthesis prompts that
   acknowledge transcript shape — speakers, timestamps, ASR errors, length).
3. Add a transcript pre-processing node before extraction (chunking,
   disfluency strip, speaker-label normalisation).
4. Re-baseline against the eval harness with the new prefix included.

Skipping (1)–(3) and just widening the allowlist will produce
low-quality wiki pages and waste LLM budget on noise.

Direct SQL:

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
