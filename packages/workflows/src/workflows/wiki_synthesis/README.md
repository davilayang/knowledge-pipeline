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

Each entity has exactly one wiki page on disk (`data/wiki/{slug}-{shortid}.md`
— flat under `data/wiki/`, no subdirectory; shortid = first 8 hex of the
opaque surrogate `entity_id`) and one row each in `entities` (identity
record) and `pages` (synthesised-artifact record). The same entity is
mentioned by many documents over time; each new mention either creates the
page (first sighting) or merges into the existing one (subsequent sightings).

The workflow is the merge engine — it decides which entities the document
mentions, then for each one decides create-vs-update and writes the result.

## Semantic model

| Concept | Storage | Cardinality |
|---|---|---|
| Entity identity | `entities` row (entity_id PK, canonical_name, normalized_name, slug, page_type) | 1 per real-world thing |
| Synthesised page | `pages` row (FK → entities) + flat `wiki/{slug}-{shortid}.md` | 1 per entity |
| Alias | `aliases` row (normalized_alias PK, entity_id FK) | many per entity (display form + variants) |
| Source contribution (ground truth) | `page_sources` row | 1 per (entity_id, item_id, source_type) — drives `num_sources` |
| Source content_id list (display) | LLM-authored list in page frontmatter | display only, not counted |
| Processed marker | `processed_items` row | 1 per (item_id, source_type) |

**Aliases** prevent duplicates. Before extraction, the workflow snapshots the
existing alias table (plus all entity canonical names) and hands it to the LLM
as YAML context. The extractor proposes a `title` and optional `matched_id`
(never a surrogate directly). `resolve_or_mint_batch` then applies the alias
gate: exact normalized_name / normalized_alias is authoritative → reuse; a
validated `matched_id` → reuse; fuzzy is advisory only (never auto-merged).
Two documents mentioning "Pandas" and "pandas-dev" update one page, not two.

**Provenance: two layers with different semantics — be aware:**

- `page_sources` (ledger table) is the **ground truth** for which
  items have contributed to an entity. `_persist_graph` writes one
  `(entity_id, item_id, source_type)` row per successful entity in the
  same transaction as `pages`. `num_sources` is
  `COUNT(DISTINCT item_id)` from this table, not from the LLM output.
  `count_sources_for_entity` reads the committed ledger after
  `_persist_graph` so the rendered frontmatter reflects the post-commit
  count with no off-by-one.
- The LLM-authored source list in the page frontmatter is **display-only**
  — the synthesis prompt shows it to the LLM and the merge prompt is
  expected to preserve it, but it is not the source of `num_sources`. Do
  not rely on it for counts; query `page_sources` instead.

## Workflow shape

`synthesize_item` (`synthesize.py`) orchestrates the whole item end-to-end
via three plain functions:

```
synthesize_item(item, db_path, wiki_dir, rejected_entities, replay)
  │
  ├─ extract(item, db_path)
  │    snapshot entity index → call extraction LLM → build Candidates
  │    failure captured as extract_error; still persists an 'error' row
  │
  ├─ resolve_or_mint_batch(index, candidates)
  │    assign surrogate entity_id to each candidate (reuse or mint)
  │    → drop denylisted (by normalised name) → build (cand, rec, resolved) triples
  │
  ├─ for entity in entities:   (sequential loop)
  │    synthesize_entity(item, entity, sibling_ids, wiki_dir)
  │      read/merge page via synthesis LLM → parse → H2-preservation check
  │      → build WikiPage in memory; failure caught per entity, siblings continue
  │
  ├─ _persist_graph(item, db_path, new_entities, successes, new_aliases)
  │    ONE SQLite transaction: entities + pages + page_sources + aliases
  │    all-or-nothing (FK order: entities first, then pages/aliases/page_sources)
  │
  ├─ _write_pages(successes, wiki_dir, db_path)
  │    write .md files after graph commits; num_sources read from committed ledger
  │
  └─ _mark_processed(item, db_path, status, error_text)
       write processed_items row LAST (crash-safe: a missing processed row
       leaves the item re-queued; entities already committed reuse their
       surrogates on retry, no orphan files)
```

Entity counts per document are unbounded; entities are processed one at a
time in the sequential loop. A writer/evaluator agentic loop (where the
synthesis LLM iterates with a separate evaluator LLM) is a deferred future
option for improving page quality — not part of the current implementation.

## Update vs create per entity

Inside `synthesize_entity` (`synthesize.py`):

```
file_path = f"{entity.slug}-{shortid(entity.entity_id)}.md"
page_path = wiki_dir / file_path   # flat under wiki_dir, no subdirectory

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
`processed_items.error`. Other entities in the same document continue.

## State boundary: filesystem vs SQLite (wiki.db)

| Lives on disk (`data/wiki/`) | Lives in SQLite (`data/wiki.db`) |
|---|---|
| `{slug}-{shortid}.md` — the rendered page (flat, no subdirs) | `entities` — identity record (entity_id PK, canonical_name, normalized_name, slug, page_type) |
| `index.md` — table of contents (regenerated) | `pages` — synthesised-artifact metadata (FK → entities); page_type/slug/canonical_name read via join |
| | `aliases` — entity name → id mapping (normalized_alias PK) |
| | `page_sources` — deterministic (entity, item) contribution ledger; drives `num_sources` |
| | `processed_items` — per (item_id, source_type) completion markers |

**Disk is the human-readable surface.** The .md files are what humans read,
diff in git, and reference. They're authored by the synthesis LLM, not
hand-edited.

**SQLite is the dedup/lineage truth.** When the schedule asks "what's
already done?", it reads `processed_items`, not the disk.
When the extractor asks "which entities exist?", it reads `aliases`,
not file listings. This separation lets the workflow be retry-idempotent
without depending on filesystem state being perfectly consistent with the DB.

**Atomicity is per-system.** Inside `_persist_graph`, all SQLite writes
(`entities` + `pages` + `page_sources` + `aliases`) are one transaction —
either all land or none do. `_mark_processed` (the `processed_items` row) is
written separately, after the graph commits and the .md files are written.
Disk writes are atomic per-file (tmp + os.replace). SQLite and disk together
are *not* atomic — but the crash-safe ordering (graph → files →
processed_items) ensures a crash leaves a recoverable state: entities are
committed (retry reuses the same surrogates, no orphan files), and the item
stays un-processed so it re-queues and the write is retried.

## Failure model

The workflow doesn't propagate failures up to Dagster — it records them.

| Failure | Caught by | Recorded as |
|---|---|---|
| Extraction LLM error / SQLite read fails | `extract` try/except | `processed_items.status='error'` with extract_error message |
| Single entity's synthesis fails | `synthesize_entity` try/except | `processed_items.error` carries `"<entity_id>: <error>"`; status still `'ok'` if siblings succeeded |
| All entities fail | (same as above, in synthesize_item's status logic) | `processed_items.status='error'` |
| `_persist_graph` SQLite transaction fails | uncaught | Dagster sees the partition fail; retry re-runs the item from scratch — there are no checkpoints to resume from |

The "swallow into state" pattern is deliberate: a partial wiki-quality issue
shouldn't look like an infrastructure failure to Dagster.

## Files

| File | Role |
|---|---|
| `synthesize.py` | Canonical entry point — `synthesize_item` + `extract` + `synthesize_entity` + `persist` |
| `prompts.py` | Extraction + create + update prompt templates |
| `parsing.py` | Parse LLM page output, slug helpers, H2 preservation check |
