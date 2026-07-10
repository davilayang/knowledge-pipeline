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
        ▼  (deps ordering only — hints resolve against the freshest entities)
promote_notes  (attach user-promoted notes (data/notes/*.md, promote: true) to canonical wiki
        │       entities as one `derived` claim per note, linked to every entity its
        │       relevance-ordered `entities` hints resolve to (exact-name + alias, alias-aware;
        │       a miss mints a `concept` entity). Idempotent + reconciling — an edited note
        │       REPLACES its claim, an unpromoted/deleted note's claim is removed. Returns the
        │       dirty count (changed + removed). Serialized on WIKI_WRITE_POOL.)
        ▼  (passes both attribute_claims' persisted count and promote_notes' dirty count)
render_pages  (re-render every page-worthy entity (≥2 claims OR ≥2 sources, or ≥1 derived note
        │      claim) from wiki.db to data/wiki/{slug}-{shortid}.md. SKIPS entirely when BOTH
        │      upstream signals are zero — a no-op render would rewrite every page's updated_at
        │      and churn the downstream curation push. Serialized on WIKI_WRITE_POOL.)
        ▼  (deps ordering only)
build_index  (rebuild the whole-wiki index sidecars from wiki.db: data/wiki/_index/resolve.json
              — alias→entity_id resolution + per-entity orientation {name,type,file,num_sources,
              page_hash} for the newsletter-assistant bridge — and data/wiki/index.md — human TOC
              grouped by live entity_type. Writes each file only when its content changed
              (snapshot_id for resolve.json, byte-equality for index.md) and self-heals a missing
              file, so it always runs (no empty-sweep gate). Serialized on WIKI_WRITE_POOL.)
```

`build_index` reads `wiki.db` fresh (no value from `render_pages`; the dep is
ordering only) and writes `resolve.json` **last** so a consumer never reads an
alias/entity whose `.md` page isn't on disk yet. An alias collision (one
lowercased key owned by two entity_ids) fails the asset.

## Incremental watermark

`attribute_claims` is a full sweep but does incremental work. Each source carries
a `wiki.sources.synthesized_at` column = the `max(extracted_at)` the last sweep
consumed for it. A source is skipped iff it already has a watermark AND its
extraction docs haven't advanced past it. `extracted_at` advances on *every*
re-extraction (re-fetch OR a prompt/model re-run without re-fetch), so a
re-extracted source re-processes; claims are deleted-then-reinserted so the page
reflects only the current extraction (replace, not merge).

## Concurrency

All four assets carry the shared `WIKI_WRITE_POOL` op tag (`config.WIKI_WRITE_POOL`,
also bound by `sync_wiki_curation`'s ops) so a synthesis write never interleaves
with a curation write against the single-writer `wiki.db`. Load-bearing: the
serialization only holds because `configs/dagster.yaml` caps the pool at one
concurrent op (`concurrency.pools.granularity: op, default_limit: 1`).

## Resources

- `queue_store` — its own `QueueStoreResource` (queue.db reader), a pipeline-scoped
  key so it doesn't collide with `fetch_extract_queue`'s `store` (Dagster's merge
  forbids two sub-Definitions binding the same key), mirroring triage's
  `triage_store`.
- `notes` — its own `NotesResource` (`get_notes_dir()` → `BACKUP_SRC_DIR/notes`, the
  NA-owned notes dir synced by the backup pipeline), bound in this pipeline's
  `Definitions` (not shared) and consumed by `promote_notes`.
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
