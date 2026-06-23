# `sync_wiki_curation` runbook

Two-way sync between the durable `wiki.db` and the Notion **"Wiki Pages"** DB —
the human-facing curation surface (browse + reject, incl. mobile). One scheduled
tick per day (07:00 UTC) = one Dagster run = full pull → push cycle.

This DAG is the **only** place Notion "Wiki Pages" I/O lives. Synthesis itself
reads the local `rejected_entities` table, never Notion (so synthesis never
depends on Notion being reachable). The denylist *semantics* live entirely in
`domains.wiki` (`reject_entity` / `upsert_rejected`); this DAG adds only the
Notion I/O on top.

## DAG (per scheduled tick)

```
schedule run_daily_sync_wiki_curation   (cron 0 7 * * *)
  │  bare RunRequest — non-partitioned (operates on the LIVE wiki.db /
  │  Notion state, not a date snapshot). Fires after the 06:00 synthesis
  │  tick; the shared concurrency key still serialises if synthesis runs long.
  ▼
wiki/rejections_pulled   (key: wiki/rejections_pulled — Notion → wiki.db)
  │  query_rejected() → every Rejected=true row {normalized_title:
  │  {category, reason}}. For each name:
  │    • live entity  → reject_entity (alias-family tombstone + cascade
  │                      delete + unlink .md)
  │    • absent       → upsert_rejected only (ensure the tombstone row)
  │  Idempotent — re-running chases the human's latest toggles, no double
  │  effect. Runs FIRST so the rejected set is gone before the push.
  ▼
wiki/pages_pushed   (key: wiki/pages_pushed — wiki.db → Notion)
     dep: wiki/rejections_pulled (never re-push a row we just deleted).
     Reads the live Notion schema once → fails loud on a missing producer
     column (drift guard). Lists all rows once (paginated, archived skipped),
     then upserts every page-backed wiki.db entity as Page status=active,
     keyed on the Entity ID column (= wiki.db's surrogate entity_id). A Notion
     row whose entity has left wiki.pages (rejected/merged/removed) is flipped
     to Page status=orphaned — the row + the curator's annotation are kept;
     the browse view filters to active. Writes ONLY producer columns; NEVER
     the curator columns.
```

Both assets share **synthesize_wiki's** `dagster/concurrency_key`
(`synthesize-wiki`, via `WIKI_DB_CONCURRENCY_KEY`) — NOT a separate per-DAG key.
SQLite is single-writer; the shared key serialises curation writes against a
synthesis persist so a mid-flight persist can't FK-error against a concurrent
delete. **This is the single most important correctness control here.**

## Notion "Wiki Pages" DB — column ownership

Data source id: `NOTION_WIKI_PAGES_DATA_SOURCE_ID`
(`385d130d-6131-8051-ad03-000b53cb61ef`). The DB already has all ten columns;
the push must match these names/types exactly (the drift guard fails the run if
a producer column is missing). Columns split by writer:

| Column | Type | Writer | Notes |
|---|---|---|---|
| `Title` | title | **push** | = `entities.canonical_name` |
| `Entity ID` | text | **push** | upsert key = wiki.db's surrogate `entity_id` (`e_<hex>`) |
| `Summary` | text | **push** | from the page's `.md` frontmatter |
| `Source count` | number | **push** | curation signal (1-source ⇒ likely noise) |
| `Page type` | select | **push** | `concept`/`tool`/`trend` (+ open-domain types, auto-created on write) |
| `Last updated` | date | **push** | page `updated_at` |
| `Page status` | select | **push** | `active` (page-backed) / `orphaned` (entity left wiki.pages); browse view filters to active |
| `Rejected` | checkbox | **human only** | read by pull; push NEVER writes it |
| `Reject category` | select | **human only** | read by pull |
| `Reject reason` | text | **human only** | read by pull |

The push reads the live schema each run (`fetch_property_names`) and fails the
run if any **push** column is missing — a human renaming/removing one is caught
loudly instead of silently dropping data.

> Note: the `Entity ID` column's stored description (`e.g. tool__claude_code`)
> predates the switch to UUID surrogate ids and is stale — the value written is
> the `e_<hex>` surrogate. No schema change needed; only the description text.

## Operating notes

- **Rate limit ~3 req/s.** ~150 entities is fine today; a persistent
  `entity_id → page_id + payload_hash` cache to push only *changed* rows is a
  deferred optimisation (skip until row count or churn hurts).
- **To remove an entity, tick `Rejected` — don't archive the Notion row.**
  Rejected is the authoritative removal signal: the pull deletes the entity from
  `wiki.db`, then the push flips its row to `Page status=orphaned` (keeping the
  row + your reject annotation; the browse view hides it). Merely archiving/
  trashing a row in Notion does NOT stick — archived rows read as *absent*, so
  the next push recreates an `active` row for any still-live entity. Reject,
  don't archive.
- **Undo a rejection:** un-tick `Rejected` in Notion does NOT auto-restore the
  entity (v1). To bring it back, remove its `rejected_entities` row
  (`sqlite3 wiki.db "DELETE FROM rejected_entities WHERE normalized_name=…"`)
  and let synthesis re-mint it on the next article that mentions it.
- **Live wire shape unvalidated by tests.** The Notion client is mocked in unit
  tests, so `pages.create` / `pages.update` / `data_sources.retrieve` need one
  live smoke run against the real "Wiki Pages" DB before the first scheduled tick.
- **Never run against prod during the 06:00 synthesis window** — the shared
  concurrency key enforces it, and the 07:00 offset keeps them from queueing
  head-to-head.
