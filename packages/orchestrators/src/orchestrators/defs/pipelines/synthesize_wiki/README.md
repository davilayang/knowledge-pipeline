# `synthesize_wiki` runbook

LangGraph-driven synthesis of `raw_store` items into a structured wiki
(concepts, tools, trends) backed by Postgres. One Dagster partition per
source content item; the graph fans out per extracted entity via the
LangGraph Send API and commits all writes (`wiki.pages` +
`wiki.aliases` + `wiki.processed`) in a single transaction.

Manually triggered — cost-aware, no cron. The asset job exists for
named-launch grouping in the UI; run it when you want to ingest.

## DAG (per partition)

Failure cascade — what blocks what when a step fails:

```
discover_pending_contents   (key: wiki/pending_contents — unpartitioned, manual)
  │  scans raw_store.db (SQLite) ∖ wiki.processed (PG)
  │  → registers unseen item_ids as wiki_items dynamic partitions
  ▼
synthesize_item   (key: wiki/synthesized — partition: <item_id>)
  │  extract_entities ─→ Send-fan-out: process_entity (×N) ─→ commit
  │
  │     pages + aliases + wiki.processed all written in ONE PG
  │     transaction. Aliases use ON CONFLICT DO NOTHING for cross-
  │     partition concurrency safety.
  │
  │  ↻ retry on the same partition auto-resumes from the LangGraph
  │    checkpoint — no LLM re-calls if the prior failure was in commit.

regenerate_toc   (key: wiki/index — independent, see note below)
  reads wiki.pages → writes data/wiki/index.md (table of contents)
```

- **`discover_pending_contents` fails** (raw_store path missing, PG
  unreachable) → no partitions registered → `synthesize_item` has
  nothing to run on.
- **`synthesize_item` fails on partition X** → only X. Checkpointer
  state persists; retry resumes from the last successful node (skipping
  LLM calls already past). Other partitions and the TOC are unaffected.
- **`regenerate_toc` fails** → `wiki.pages` is still authoritative;
  `data/wiki/index.md` goes stale until next run.

> **Why `regenerate_toc` is not `deps=[wiki/synthesized]`**: with a
> growing `DynamicPartitionsDefinition`, Dagster's default
> `AllPartitionMapping` would block the TOC forever — every
> `discover_pending_contents` run registers fresh partitions that never
> materialize. Re-materialize the TOC manually after a synthesis batch.
> Phase E swaps this for a sensor that fires on each successful
> `wiki/synthesized` partition.

## Operations

### Run a synthesis batch

```bash
# 1. Discover new raw_store items and register them as partitions.
dg launch -m orchestrators.defs.pipelines.definitions \
  --asset-selection wiki/pending_contents

# 2. Materialize the new partitions. UI is easier here:
#    Assets → group `wiki` → wiki/synthesized → select partitions →
#    Materialize. Concurrency is throttled by the pipeline's
#    op_tags concurrency_key — fan-out is bounded.

# 3. Regenerate the table of contents after the batch lands.
dg launch -m orchestrators.defs.pipelines.definitions \
  --asset-selection wiki/index
```

`discover_pending_contents` respects `WikiResource.max_articles` (default
50) — only that many new partitions are added per run, regardless of how
many items are actually pending. Subsequent runs pick up the next batch.
Set to `0` to disable the cap.

### Re-process a single item from scratch

A partition retry auto-resumes from the LangGraph checkpoint. To force a
clean re-run (e.g. you changed prompts and want fresh LLM output):

```sql
-- Drop the processed marker so discover_pending_contents picks it up again.
DELETE FROM wiki.processed WHERE item_id = '<item_id>';

-- (Optional) drop the LangGraph checkpoint so it doesn't auto-resume.
DELETE FROM checkpoints       WHERE thread_id = 'wiki_synthesis__<item_id>';
DELETE FROM checkpoint_writes WHERE thread_id = 'wiki_synthesis__<item_id>';
DELETE FROM checkpoint_blobs  WHERE thread_id = 'wiki_synthesis__<item_id>';
```

Then re-trigger `wiki/pending_contents` and materialize the partition.

### Workflow failed mid-flight (LLM error, PG hiccup)

Just retry the partition. The runner detects an in-progress checkpoint
(`graph.get_state(config).next` is non-empty) and resumes — already-
completed nodes (including any LLM calls past the checkpoint) are skipped.
A failure in `commit` retries only the PG transaction.

### LLM rate limit / quota

The Send fan-out runs entity sub-graphs in parallel. The pipeline's
shared `concurrency_key` op tag throttles cross-partition parallelism, but
within a single partition the fan-out width = number of extracted
entities. If you're hitting OpenAI 429s:

- Lower the per-pipeline concurrency at the Dagster instance level.
- Reduce `max_articles` so fewer partitions queue at once.
- Wait — the runner doesn't catch 429; the partition fails, you retry,
  the checkpointer resumes from where it stopped.

### TOC missing or stale

Just re-materialize `wiki/index`. `regenerate_toc` reads everything in
`wiki.pages` and overwrites `data/wiki/index.md` from scratch —
idempotent.

### Inspecting state

```sql
-- What's been processed, with status.
SELECT status, COUNT(*) FROM wiki.processed GROUP BY status;

-- Failed partitions (workflow caught error, committed an error marker).
SELECT item_id, error FROM wiki.processed WHERE status = 'error';

-- Pages by type.
SELECT page_type, COUNT(*) FROM wiki.pages GROUP BY page_type;
```

A partition with `status='error'` is a *successful* asset run from
Dagster's perspective — the workflow handled the failure and recorded it.
Distinct from a Dagster run failure (LLM exception that wasn't caught,
PG unreachable, etc.).

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
set, every LangGraph run is traced. Each `wiki/synthesized` partition
appears as one trace named `wiki_synthesis__<item_id>` with sub-spans for
`extract_entities`, each `process_entity`, and `commit`.

If the Langfuse env vars are unset, the workflow runs fine without
tracing — no errors, no warnings.

### Postgres

`DATABASE_URL` points at the `knowledge_pipeline` database. Schema is
applied by `domains/wiki/schema/wiki.sql` (run once per environment).
The same Postgres instance hosts LangGraph checkpoints (separate tables
managed by `langgraph-checkpoint-postgres`); no extra setup beyond the
URL.
