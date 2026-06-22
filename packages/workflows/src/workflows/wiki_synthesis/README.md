# `wiki_synthesis` workflow

A plain-function workflow that turns one source document into incremental
updates to a structured wiki. One call to `synthesize_item` handles one
document; the Dagster asset that wraps it
(`packages/orchestrators/src/orchestrators/defs/synthesize_wiki/assets.py:synthesized`)
runs one call per pending item in a scheduled tick.

For operations (how to launch, retry, debug), see the asset's runbook:
`packages/orchestrators/src/orchestrators/defs/synthesize_wiki/README.md`.
This document is for engineers modifying the workflow.

## What the workflow does

Given a source document (an `IngestItem` — id, title, full text, source_type),
produce updates to a structured wiki of three entity types:

- **concept** — abstract ideas (e.g. "vector quantization")
- **tool** — concrete software/products (e.g. "DuckDB")
- **trend** — emerging patterns or shifts (e.g. "MoE architectures")

Each entity has exactly one wiki page on disk (`data/wiki/<page_type>/<slug>.md`)
and one row in `pages`. The same entity is mentioned by many documents
over time; each new mention either creates the page (first sighting) or merges
into the existing one (subsequent sightings).

The workflow is the merge engine — it decides which entities the document
mentions, then for each one decides create-vs-update and writes the result.

## Semantic model

| Concept | Storage | Cardinality |
|---|---|---|
| Entity | `pages` row + `wiki/<type>/<slug>.md` file | 1 per real-world thing |
| Alias | `aliases` row | many per entity (canonical name + variants) |
| Source contribution (ground truth) | `page_sources` row | 1 per (entity_id, item_id, source_type) — drives `num_sources` |
| Source content_id list (display) | `pages.sources[]` element | LLM-authored list in frontmatter — display only, not counted |
| Source type | `pages.source_types[]` (last writer wins) | which source domain last touched the entity |
| Processed marker | `processed` row | 1 per (item_id, source_type) |

**Aliases** prevent duplicates. Before extraction, the workflow snapshots the
existing alias table and hands it to the LLM as YAML context. The extractor
returns `is_new=False` for entities that match an existing alias — the same
canonical id is reused, so two documents mentioning "Pandas" and "pandas-dev"
update one page, not two.

**Provenance: three columns with different semantics — be aware:**

- `page_sources` (ledger table) is the **ground truth** for which
  items have contributed to an entity. `persist` writes one
  `(entity_id, item_id, source_type)` row per successful entity in the
  same transaction as `pages`. `num_sources` is
  `COUNT(DISTINCT item_id)` from this table, not from the LLM output.
  `is_source_for_entity` lets `synthesize_entity` add +1 pre-commit so
  the rendered frontmatter reflects the post-commit count even on first
  sighting.
- `pages.sources[]` is the LLM-authored list of source content_ids
  in the page frontmatter. It is **display-only** — the synthesis update
  prompt shows it to the LLM and the merge prompt is expected to preserve
  it, but it is no longer the source of `num_sources`. Do not join against
  it for counts; query `page_sources` instead.
- `pages.source_types[]` is **not** cumulative. `persist` always passes
  `source_types=[item.source_type]` (single element) and the upsert
  replaces the column outright. So this column reflects only the most
  recent writer's source domain, not the union across history.
  Promote `source_types` to additive semantics if that pattern shows up
  often.

## Workflow shape

`synthesize_item` (`synthesize.py`) orchestrates the whole item end-to-end
via three plain functions:

```
synthesize_item(item, db_path, wiki_dir, rejected_entities, replay)
  │
  ├─ extract(item, db_path, rejected_entities)
  │    snapshot aliases → call extraction LLM → drop denylisted → stage aliases
  │    failure captured as extract_error; still persists an 'error' row
  │
  ├─ for entity in entities:   (sequential loop)
  │    synthesize_entity(item, entity, sibling_ids, wiki_dir, db_path)
  │      read/merge page via synthesis LLM → parse → H2-preservation check
  │      → write .md atomically; failure caught per entity, siblings continue
  │
  └─ persist(item, db_path, successes, staged_aliases, status, error_text)
       ONE SQLite transaction: pages + page_sources + aliases + processed
       all-or-nothing
```

Entity counts per document are unbounded; entities are processed one at a
time in the sequential loop. A writer/evaluator agentic loop (where the
synthesis LLM iterates with a separate evaluator LLM) is a deferred future
option for improving page quality — not part of the current implementation.

## Update vs create per entity

Inside `synthesize_entity` (`synthesize.py`):

```
page_path = wiki_dir / entity.page_type / f"{slug}.md"

if page_path.exists():
    # update path: LLM merges new content into existing page
    prompt = PAGE_SYNTHESIS_USER_UPDATE   # includes existing page text
    ...
    check_h2_preservation(page_path, new_content)   # H2 sections must stay
else:
    # create path: LLM writes fresh page with standard sections
    prompt = PAGE_SYNTHESIS_USER_CREATE
```

The H2 preservation check is the safety net: the synthesis LLM is allowed to
*expand* sections (add bullets, refine prose) but not *delete* them. Deletes
fail the entity, which is recorded as a per-entity error and surfaces in
`processed.error`. Other entities in the same document continue.

## State boundary: filesystem vs SQLite (wiki.db)

| Lives on disk (`data/wiki/`) | Lives in SQLite (`data/wiki.db`) |
|---|---|
| `<page_type>/<slug>.md` — the rendered page | `pages` — page metadata + provenance |
| `index.md` — table of contents (regenerated) | `aliases` — entity name → id mapping |
| | `page_sources` — deterministic (entity, item) contribution ledger; drives `num_sources` |
| | `processed` — partition completion markers |

**Disk is the human-readable surface.** The .md files are what humans read,
diff in git, and reference. They're authored by the synthesis LLM, not
hand-edited.

**SQLite is the dedup/lineage truth.** When the schedule asks "what's
already done?", it reads `processed`, not the disk.
When the extractor asks "which entities exist?", it reads `aliases`,
not file listings. This separation lets the workflow be retry-idempotent
without depending on filesystem state being perfectly consistent with the DB.

**Atomicity is per-system.** Inside `persist`, all SQLite writes (`pages` +
`aliases` + `page_sources` + `processed`) are one transaction —
either all land or none do. Disk writes are atomic per-file (tmp + os.replace). SQLite and disk
together are *not* atomic — if the SQLite commit fails after .md files are written,
the files are stranded but get rewritten on replay. The replay path is
write_page-idempotent for identical content.

## Failure model

The workflow doesn't propagate failures up to Dagster — it records them.

| Failure | Caught by | Recorded as |
|---|---|---|
| Extraction LLM error / SQLite read fails | `extract` try/except | `processed.status='error'` with extract_error message |
| Single entity's synthesis fails | `synthesize_entity` try/except | `processed.error` carries `"<entity_id>: <error>"`; status still `'ok'` if siblings succeeded |
| All entities fail | (same as above, in synthesize_item's status logic) | `processed.status='error'` |
| `persist` SQLite transaction fails | uncaught | Dagster sees the partition fail; retry re-runs the item from scratch — there are no checkpoints to resume from |

The "swallow into state" pattern is deliberate: a partial wiki-quality issue
shouldn't look like an infrastructure failure to Dagster.

## Files

| File | Role |
|---|---|
| `synthesize.py` | Canonical entry point — `synthesize_item` + `extract` + `synthesize_entity` + `persist` |
| `prompts.py` | Extraction + create + update prompt templates |
| `parsing.py` | Parse LLM page output, slug helpers, H2 preservation check |
