# `synthesize_wiki` runbook

Plain-function wiki synthesis pipeline: turns `raw_store` items into a
structured wiki (concepts, tools, trends) backed by a local SQLite file
(`data/wiki.db`). One scheduled tick per day (06:00 UTC) = one Dagster
run = full pending → extracted → synthesized → index cycle.

## DAG (per scheduled tick)

Failure cascade — what blocks what when a step fails:

```
schedule run_daily_synthesize_wiki   (cron 0 6 * * *)
  │  fires partition (D-1) on day D — same key as backup_readings'
  │  03:00 UTC materialisation. Bare RunRequest, no run_config.
  ▼
wiki/pending   (key: wiki/pending — daily partition, key = data-date)
  │  dep: snapshots/raw_store (default IdentityPartitionMapping — same key)
  │  reads BACKUP_DST_DIR/<partition_key>/raw_store.db; raises dg.Failure if
  │  the file is absent (backup_readings hasn't materialised that partition).
  │  Filters raw_store content_ids by ALLOWED_CONTENT_ID_PREFIXES (today:
  │  "medium::" only — current prompts assume article-shape inputs); then
  │  drops items whose content_md is NULL or blank (unfetched — synthesis
  │  must not permanently mark an empty document processed before the
  │  fetcher fills it); reads eligible IDs ∖ processed; output is the
  │  capped work order (≤ WIKI_MAX_PER_TICK). Metadata exposes total_pending
  │  (pre-cap), queued (post-cap), capped (bool), excluded_by_source,
  │  excluded_unfetched — daily backlog timeseries.
  ▼
wiki/extracted   (key: wiki/extracted — daily partition)
  │  in: pending (list[str] from wiki/pending via Dagster IO manager)
  │  runs the extraction LLM (call #1) per item via extract_item; emits a
  │  per-item map {item_id: {candidates, extract_error}}. Candidates are
  │  UNRESOLVED names (name/page_type/matched_id/aliases) — no minting here.
  │  Writes NO DB state, so it can't create atomicity hazards. Metadata
  │  surfaces candidate_count + extraction cost SEPARATELY from synthesis.
  ▼
wiki/synthesized   (key: wiki/synthesized — daily partition)
  │  in: extracted ({item_id: {candidates, extract_error}} via Dagster IO)
  │  derives the same snapshot path from its own partition_key (re-reads the
  │  items for their content); iterates sequentially through
  │  synthesize_extracted_item. No re-filter — the commit txn is idempotent
  │  (ON CONFLICT) so a retry re-processes already-committed items at the
  │  cost of duplicate LLM spend. Items are processed sequentially.
  │
  │     resolve_or_mint_batch (LIVE entity index — minting/dedup happens
  │     here, not in extracted, so cross-item dedup is correct even though
  │     extraction ran in the prior stage; matched_id is advisory)
  │             ─→ synthesize_entity (call #2, sequential loop)
  │             ─→ _persist_graph (ONE transaction: entities + pages +
  │                page_sources + aliases) ─→ write .md files ─→
  │                _mark_processed (processed_items row, written LAST).
  │     Aliases use ON CONFLICT DO NOTHING for cross-item safety;
  │     page_sources uses ON CONFLICT DO NOTHING (idempotent under retries).
  │
  │  ↻ retry on the same date partition re-extracts + re-synthesizes;
  │    per-item dedup is via the processed ledger (wiki/pending skips
  │    item_ids already ok/skipped); failed items re-run from scratch.
  ▼
wiki/index   (key: wiki/index — daily partition; deps wiki/synthesized)
  reads pages → writes data/wiki/index.md (table of contents)
```

Extraction (call #1) and synthesis (call #2) are separate asset nodes — each
carries its own cost/latency metadata, and synthesis can be re-materialised off
the stored candidate artifact. The split line is at *candidates, not
resolution*: `wiki/extracted` hands off unresolved names; `wiki/synthesized`
mints/dedups against a live index, which preserves within-run dedup.

- **Snapshot missing** for the wiki partition's key (backup_readings didn't
  run, or its partition for that date hasn't materialised) → `wiki/pending`
  raises `dg.Failure` with the expected path. The schedule still fires;
  there's no fallback to an older snapshot. Fix: run `backup_readings` for
  that partition (or wait for the next 03:00 UTC tick if it'll catch up
  naturally).
- **`wiki/pending` empty list** → `wiki/extracted` short-circuits to an empty
  map (`_no pending items this tick_`) and `wiki/synthesized` to a no-op
  (`_no extracted items this tick_`). Run is green; no LLM calls.
- **`wiki/synthesized` per-item LLM failures** → swallowed into
  `processed_items` with `status='error'`; the run continues other items.
  The Dagster run shows green.
- **`wiki/synthesized` run-level failures** (an item raises out of the
  workflow — auth, infra) → the asset raises `dg.Failure`, the run fails,
  Dagster retry replays the pickled pending list. Items that already have a
  `processed_items` row with `status='ok'` are skipped by `wiki/pending`; other
  items re-run from scratch.
- **`wiki/index` fails** → today's partition for `wiki/index` stays
  unmaterialized; `pages` remains authoritative; re-materialize the
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
dg launch --job synthesize_wiki -m orchestrators.defs.definitions \
  --partition $(date +%Y-%m-%d)
```

### Re-process a single item from scratch

Delete the processed marker so the next schedule tick picks it up again:

```bash
sqlite3 data/wiki.db "DELETE FROM processed_items WHERE item_id = '<item_id>';"
```

There are no checkpoints to drop — each run is a fresh execution.

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
run. Each item processes its extracted entities sequentially inside one
`synthesize_item` call. If you're hitting OpenAI 429s:

- Lower `WIKI_MAX_PER_TICK` in `def_config.py` so fewer items queue
  per tick.
- Wait — the runner doesn't catch 429; the run fails, you retry, items
  already recorded in `processed_items` as `status='ok'` are skipped by
  `wiki/pending`, so only remaining items re-run.

### TOC missing or stale

Re-materialize `wiki/index` for today's partition. It reads everything in
`pages` and overwrites `data/wiki/index.md` from scratch — idempotent.

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
- An item whose prior attempt **completed successfully** (status='ok' in
  `processed_items`) → skipped by `wiki/pending` on retry. No LLM calls re-paid.
- An item whose prior attempt **failed or errored** → re-processed in
  full on retry. LLM cost re-paid; the commit txn is idempotent (ON
  CONFLICT), so no DB conflict, but expect double-billing on the re-run
  items.

This is a deliberate trade — the alternative (re-filtering against
`processed_items` inside `synthesized`) reaches back into state that
`wiki/pending` already owns. Worst-case cost amplification is bounded
(`max_retries=1`, `WIKI_MAX_PER_TICK=30`).

When the run completes with `errors > 0`, the `cost_complete` metadata
boolean is `false`: per-item failures may have racked up LLM calls before
raising, and those calls aren't reflected in `cost_usd` for this
materialization. Refer to Langfuse for ground-truth per-trace cost in that
scenario.

### Inspecting state

`wiki/pending` materialization metadata is the daily backlog reading:
`total_pending` (pre-cap, eligible only), `queued` (post-cap, what got
synthesized), `capped` (bool — `true` if the queue exceeded
`WIKI_MAX_PER_TICK`), `excluded_by_source` (raw_store rows skipped
because their content_id prefix isn't in `ALLOWED_CONTENT_ID_PREFIXES`),
`excluded_unfetched` (allowed rows skipped because `content_md` is NULL or
blank — the fetcher hasn't filled them yet).
If `capped` stays `true` for more than a few days you're falling behind —
either raise the cap, increase tick frequency, or both. If
`excluded_by_source` is large you've got non-article sources accumulating
that the current prompts won't handle well — see "Adding new sources" below
before adding their prefix to the allowlist. If `excluded_unfetched` is
large, the fetcher is lagging behind ingestion — check the fetch pipeline.

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

Direct SQL (sqlite3 against `data/wiki.db`):

```bash
# What's been processed, with status.
sqlite3 data/wiki.db "SELECT status, COUNT(*) FROM processed_items GROUP BY status;"

# Failed items (workflow caught error, committed an error marker).
sqlite3 data/wiki.db "SELECT item_id, error FROM processed_items WHERE status = 'error';"

# Pages by type (page_type lives on entities; join required).
sqlite3 data/wiki.db "SELECT e.page_type, COUNT(*) FROM pages p JOIN entities e ON e.entity_id = p.entity_id GROUP BY e.page_type;"
```

A row with `status='error'` is a *successful* asset run from Dagster's
perspective — the workflow handled the failure and recorded it. Distinct
from a Dagster run failure (auth error, wiki.db unreachable, etc.).

## External setup

### LLM API key

The workflow uses two OpenAI models (configurable in
`packages/workflows/src/workflows/wiki_synthesis/synthesize.py`):

- `gpt-4.1-nano` — entity extraction (`EXTRACTION_MODEL`)
- `gpt-4.1-mini` — page synthesis (`SYNTHESIS_MODEL`)

Set `OPENAI_API_KEY` in the server's `.env`. No fallback — an unset key
fails at the first `generate(...)` call inside the workflow.

### Langfuse tracing (optional)

When `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` are
set, each item is traced via the `langfuse.openai` OpenAI drop-in. One
span is opened per item named `wiki_synthesis__<item_id>` (session_id set
to `item_id`, tags `["wiki_synthesis", <source_type>, "fresh"|"replay"]`);
the 1 extraction call and N synthesis calls nest under it automatically as
generation observations. The asset calls `flush_langfuse()` after the batch.

If the Langfuse env vars are unset, the workflow runs fine without
tracing — no errors, no warnings.

### SQLite (wiki.db)

`wiki_db_path` in `WikiResource` defaults to `DATA_DIR/"wiki.db"`.
`get_db_path()` ensures the schema is applied idempotently (all DDL uses
`IF NOT EXISTS`) before any asset touches the file. No manual schema
application is required.

The file lives under the host-bind-mounted `./data` directory and survives
`docker compose down -v`.

Schema change / rebuild: delete (or rename) `data/wiki.db` and
re-synthesize. The schema is re-applied by `get_db_path()` on next run.

### Entity rejection list (denylist)

`synthesized` reads a curator-managed denylist from the Notion "Wiki Pages"
database (`WikiPagesNotionResource.query_rejected`) at the start of each
tick: every row with `Rejected` ticked contributes its normalised page
`Title` (the entity's canonical name), and candidates whose extracted
normalised name OR resolved entity's normalised_name matches are dropped
before synthesis. The surrogate `entity_id` is minted post-extraction so
the denylist keys on the name the curator sees, not an id. The Notion DB
is the edit surface — see the curator columns `Rejected` / `Reject
category` / `Reject reason`.

Resolved behind a **fail-closed** loader (`denylist.load_rejected_entities`):
a successful read atomically refreshes `data/wiki/_index/rejected.json`; a
Notion error reuses that last-known-good snapshot rather than falling back
to an empty list (an empty denylist would silently re-admit rejected
entities). Empty is reachable only on first-run bootstrap, with a loud log
warning.

Env: `NOTION_WIKI_PAGES_DATA_SOURCE_ID` (the "Wiki Pages" data source id) +
the shared `NOTION_INTEGRATION_TOKEN`.
