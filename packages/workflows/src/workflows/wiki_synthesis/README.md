# `wiki_synthesis` workflow

A plain-function workflow that turns one source document into incremental
updates to a structured wiki. The Dagster `wiki/extracted` asset calls
`extract_item` per pending item; `wiki/synthesized` then calls
`synthesize_extracted_item` per item to resolve, synthesize, and persist.
`synthesize_item` (= extract + synthesize in one call) is a convenience
wrapper kept for callers that don't split the two stages.

For operations (how to launch, retry, debug), see the asset's runbook:
`packages/orchestrators/src/orchestrators/defs/synthesize_wiki/README.md`.
This document is for engineers modifying the workflow.

## What the workflow does

Given a source document (an `IngestItem` — id, title, full text, source_type),
produce updates to a structured wiki. The `PageType` literal (`domains.wiki.types`)
covers: `concept`, `tool`, `trend`, `person`, `organization`, `method`, `dataset`,
`other`. The extraction prompt is domain-agnostic — entity-count guidance caps at
15 (enforced by `ExtractionResult.entities max_length=15`), with quality-over-count
framing.

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
| Page edition history | `page_versions` row (full body + provenance) | 1 per synthesis that **changes** the page's `{summary, content}` |
| Alias | `aliases` row (normalized_alias PK, entity_id FK) | many per entity (display form + variants) |
| Source contribution (ground truth) | `page_sources` row | 1 per (entity_id, item_id, source_type) — drives BOTH `num_sources` and the rendered `sources` list |
| Co-occurrence edge | `entity_relations` row | 1 per (directed edge, contributing item) — drives the rendered `related` list (derived `co_count`) |
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
  same transaction as `pages`.
- Both the rendered `num_sources` AND the `sources` list are
  producer-authoritative, derived from this ledger after `_persist_graph`
  commits: `count_sources_for_entity` → `num_sources`,
  `get_source_ids_for_entity` → the `sources` list (accumulated distinct
  item_ids, ordered by first contribution). They stay consistent by
  construction.
- `WikiPage.sources` (the LLM-echoed `[source_id]` in the synthesis
  output) is **ignored** for rendering — it's the single triggering item,
  not the accumulated set. Query `page_sources`, never the LLM output.

**Related edges (`entity_relations`):** the rendered `related` list is also
producer-authoritative. `_persist_graph` writes a `(directed edge, item)` row
for every pair of entities an item co-mentions, in both directions (a pure,
idempotent ledger). `get_related_for_entity` derives `co_count =
COUNT(DISTINCT item_id)` per neighbour and renders the top-N strongest links
(`co_count DESC, last_seen DESC`). So `related` **accumulates across every
article** that co-mentions an entity, not just the latest one's siblings — and
like `num_sources`, the count is derived (retry-safe, no stored counter).
`WikiPage.related` (this article's siblings) is no longer rendered; `pages.related_ids`
remains as a legacy this-tick advisory column. On a page *update*, producer-owned
frontmatter (`aliases`/`related`/`sources`/`num_sources`/`updated_at`) is stripped
from the existing page before it re-enters the synthesis prompt, so the LLM never
echoes accumulated metadata back into the body.

**Edition history (`page_versions`):** `_persist_graph` appends a full-content
version row **only when the page's prose changed** — gated by a semantic hash
over `{summary, content}` (`identity.page_content_hash`) compared against the
HEAD pointer on `pages` (`content_hash` / `current_version`, read by
`get_page_head` before the upsert overwrites it). The hash deliberately excludes
`related` / `sources` / `num_sources` / `updated_at`: those are per-item or
ledger-tracked, so a re-synthesis from a different article that doesn't change
the prose updates the file + ledger but appends **no** version. The `.md` write
is unconditional (gate the append, not the file write). Read history with
`get_page_history` (metadata, newest-first) and `get_page_version` (one body).

## Workflow shape

The DAG splits extraction and synthesis into two separate entry points:

- **`extract_item`** — what `wiki/extracted` calls. Snapshots the entity
  index, relevance-filters it to the article's keyword set (via
  `domains.wiki.relevance.select_relevant_entities` — a no-op until the catalog
  exceeds `RELEVANCE_MAX_ENTITIES=50`), calls the extraction LLM (call #1),
  returns `{candidates, extract_error, llm_calls}`. Writes NO DB state.
- **`synthesize_extracted_item`** — what `wiki/synthesized` calls. Takes
  the stored extraction payload, resolves/mints against a LIVE index, then
  delegates to `_synthesize_resolved`.
- **`synthesize_item`** — convenience wrapper (extract + synthesize in one
  call) for callers that don't split the two stages. Behaviour-preserving.

`_synthesize_resolved` (the shared synthesis core) via three plain functions:

```
_synthesize_resolved(item, candidates, db_path, wiki_dir, rejected_entities)
  │
  ├─ resolve_or_mint_batch(index, candidates)
  │    assign surrogate entity_id to each candidate (reuse or mint)
  │    → drop denylisted (by normalised name) → build (cand, rec, resolved) triples
  │    (reads a LIVE entity index so within-run dedup is correct even though
  │    extraction ran in the prior asset)
  │
  ├─ for entity in entities:   (sequential loop)
  │    synthesize_entity(item, entity, sibling_ids, wiki_dir)
  │      read/merge page via synthesis LLM → parse → H2-preservation check
  │      → build WikiPage in memory; failure caught per entity, siblings continue
  │
  ├─ _persist_graph(item, db_path, new_entities, successes, new_aliases)
  │    ONE SQLite transaction: entities + pages + page_sources + aliases
  │    + page_versions (appended only when {summary, content} changed)
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

Entity counts per document are capped at 15 (enforced by `ExtractionResult`
`max_length=15`); entities are processed one at a time in the sequential loop.
A writer/evaluator agentic loop (where the synthesis LLM iterates with a
separate evaluator LLM) is a deferred future option for improving page quality
— not part of the current implementation.

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
| `synthesize.py` | Entry points: `extract_item` (extraction LLM, no DB write), `synthesize_extracted_item` (resolve + synthesize + persist), `synthesize_from_candidates` (synthesis-only from pre-extracted candidates), `synthesize_item` (end-to-end convenience wrapper) |
| `prompts.py` | Prompt loader — resolves versioned `.md` files under `prompts/wiki/` via `KP_PROMPTS_ROOT`; exposes `ENTITY_EXTRACTION_SYSTEM`, `ENTITY_EXTRACTION_USER`, `PAGE_SYNTHESIS_SYSTEM`, `PAGE_SYNTHESIS_USER_CREATE`, `PAGE_SYNTHESIS_USER_UPDATE` |
| `parsing.py` | Parse LLM page output, slug helpers, H2 preservation check |
