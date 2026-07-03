# synthesize_wiki

The wiki-write lane: turns `queue.db`'s stored extraction docs into `wiki.db`
attributed claims and renders entity pages to `data/wiki/`. The DAG boundary is
the **store seam** — everything that writes `wiki.db` lives here; the queue.db +
Notion fetch/extract path stays in `fetch_extract_queue`. This makes synthesis
source-agnostic in principle (any producer of the two extraction docs can feed it)
rather than welded to the Notion-queue fetch path.

## DAG (daily sweep, unpartitioned)

```
run_daily_synthesize_wiki  (cron 0 6 * * * — 06:00 UTC, before the 07:00 sync_wiki_curation tick)
        ▼
synthesize_wiki_job
        │
        ▼
attribute_claims  (sweep over every queue.db source with both an extract_claims and an
        │          extract_entities doc; synthesise the NEW-OR-CHANGED ones into wiki.db —
        │          source + claims + claim→entity links, mint-or-match entities.
        │          "Changed" = the source's docs' max(extracted_at) advanced past the
        │          synthesized_at watermark stored on the source; a re-extracted source is
        │          re-processed with its claims REPLACED. Fail-soft per source. Returns the
        │          persisted count. Serialized on WIKI_WRITE_POOL.)
        ▼  (passes persisted count)
render_pages  (re-render every page-worthy entity (≥2 claims OR ≥2 sources) from wiki.db to
               data/wiki/{slug}-{shortid}.md. SKIPS entirely when the sweep changed nothing —
               a no-op render would rewrite every page's updated_at and churn the downstream
               curation push. Serialized on WIKI_WRITE_POOL.)
```

## Incremental watermark

`attribute_claims` is a full sweep but does incremental work. Each source carries
a `wiki.sources.synthesized_at` column = the `max(extracted_at)` the last sweep
consumed for it. A source is skipped iff it already has a watermark AND its
extraction docs haven't advanced past it. `extracted_at` advances on *every*
re-extraction (re-fetch OR a prompt/model re-run without re-fetch), so a
re-extracted source re-processes; claims are deleted-then-reinserted so the page
reflects only the current extraction (replace, not merge).

## Concurrency

Both assets carry the shared `WIKI_WRITE_POOL` op tag (`config.WIKI_WRITE_POOL`,
also bound by `sync_wiki_curation`'s ops) so a synthesis write never interleaves
with a curation write against the single-writer `wiki.db`. Load-bearing: the
serialization only holds because `configs/dagster.yaml` caps the pool at one
concurrent op (`concurrency.pools.granularity: op, default_limit: 1`).

## Resources

- `queue_store` — its own `QueueStoreResource` (queue.db reader), a pipeline-scoped
  key so it doesn't collide with `fetch_extract_queue`'s `store` (Dagster's merge
  forbids two sub-Definitions binding the same key), mirroring triage's
  `triage_store`.
- `wiki` — the shared `WikiResource` (bound in `shared.defs`), consumed at the
  top-level `Definitions.merge`.

## Runbook

```bash
# Manually trigger the daily sweep (e.g. after a backfill or a re-extraction).
dg launch --job synthesize_wiki

# Force a full re-render regardless of the watermark: clear synthesized_at first,
# then launch (each source will look new-or-changed).
sqlite3 data/wiki.db "UPDATE sources SET synthesized_at = NULL;"
dg launch --job synthesize_wiki
```
