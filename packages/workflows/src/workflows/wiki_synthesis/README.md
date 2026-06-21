# `wiki_synthesis` workflow

A LangGraph workflow that turns one source document into incremental updates
to a structured wiki. One invocation handles one document; the Dagster asset
that wraps it (`pipelines/synthesize_wiki/assets.py:synthesized`) runs one
invocation per pending item in a scheduled tick.

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
and one row in `wiki.pages`. The same entity is mentioned by many documents
over time; each new mention either creates the page (first sighting) or merges
into the existing one (subsequent sightings).

The workflow is the merge engine — it decides which entities the document
mentions, then for each one decides create-vs-update and writes the result.

## Semantic model

| Concept | Storage | Cardinality |
|---|---|---|
| Entity | `wiki.pages` row + `wiki/<type>/<slug>.md` file | 1 per real-world thing |
| Alias | `wiki.aliases` row | many per entity (canonical name + variants) |
| Source contribution (ground truth) | `wiki.page_sources` row | 1 per (entity_id, item_id, source_type) — drives `num_sources` |
| Source content_id list (display) | `wiki.pages.sources[]` element | LLM-authored list in frontmatter — display only, not counted |
| Source type | `wiki.pages.source_types[]` (last writer wins) | which source domain last touched the entity |
| Processed marker | `wiki.processed` row | 1 per (item_id, source_type) |

**Aliases** prevent duplicates. Before extraction, the workflow snapshots the
existing alias table and hands it to the LLM as YAML context. The extractor
returns `is_new=False` for entities that match an existing alias — the same
canonical id is reused, so two documents mentioning "Pandas" and "pandas-dev"
update one page, not two.

**Provenance: three columns with different semantics — be aware:**

- `wiki.page_sources` (ledger table) is the **ground truth** for which
  items have contributed to an entity. `commit` writes one
  `(entity_id, item_id, source_type)` row per successful entity in the
  same transaction as `wiki.pages`. `num_sources` is
  `COUNT(DISTINCT item_id)` from this table, not from the LLM output.
  `is_source_for_entity` lets `process_entity` add +1 pre-commit so the
  rendered frontmatter reflects the post-commit count even on first sighting.
- `wiki.pages.sources[]` is the LLM-authored list of source content_ids
  in the page frontmatter. It is **display-only** — the synthesis update
  prompt shows it to the LLM and the merge prompt is expected to preserve
  it, but it is no longer the source of `num_sources`. Do not join against
  it for counts; query `wiki.page_sources` instead.
- `wiki.pages.source_types[]` is **not** cumulative. `commit` always passes
  `source_types=[item.source_type]` (single element) and the upsert
  replaces the column outright. So this column reflects only the most
  recent writer's source domain, not the union across history.
  Promote `source_types` to additive semantics if that pattern shows up
  often.

## Graph shape

Two-tier: a parent graph orchestrates per-document work; a sub-graph runs once
per extracted entity in parallel.

```
Parent (graph.py)              Sub-graph (entity_graph.py)
─────────────────              ────────────────────────────

START
  │
  ▼
extract_entities       ←─── reads wiki.aliases, calls extraction LLM,
  │                          stages new aliases for commit
  │
  ├── (no entities) ──→ commit  (records status='skipped')
  │
  └── Send fan-out ────→ entity_workflow ──┐
                         entity_workflow ──┤   parallel,
                         entity_workflow ──┤   one Send per entity
                                           │
                              ┌────────────┘
                              │
                              ▼
                         (operator.add reducer aggregates results)
                              │
                              ▼
                         commit       ←──── one Postgres txn:
                              │              wiki.pages, wiki.aliases,
                              ▼              wiki.page_sources, wiki.processed
                             END
```

**Why two tiers, not one node per entity in the parent:** entity counts are
unbounded per document. Modeling them as static parent nodes would require
knowing the count at graph-build time. The Send API lets us fan out at
runtime — `extract_entities` decides N, the conditional edge dispatches N
sub-graph instances, the reducer collects N results.

**Why a sub-graph at all (vs a plain function):** LangGraph checkpoints
sub-graph state in a nested namespace. If entity 7 of 12 fails, retry
re-runs only entity 7's sub-graph — entities 1-6 are skipped, no LLM calls
re-paid. With a plain function in a list comprehension, retry would re-run
all 12.

## Update vs create per entity

Inside the sub-graph (`entity_graph.py:process_entity`):

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
`wiki.processed.error`. Other entities in the same document continue.

## State boundary: filesystem vs Postgres

| Lives on disk (`data/wiki/`) | Lives in Postgres |
|---|---|
| `<page_type>/<slug>.md` — the rendered page | `wiki.pages` — page metadata + provenance |
| `index.md` — table of contents (regenerated) | `wiki.aliases` — entity name → id mapping |
| | `wiki.page_sources` — deterministic (entity, item) contribution ledger; drives `num_sources` |
| | `wiki.processed` — partition completion markers |
| | LangGraph checkpoints (separate tables) |

**Disk is the human-readable surface.** The .md files are what humans read,
diff in git, and reference. They're authored by the synthesis LLM, not
hand-edited.

**Postgres is the dedup/lineage truth.** When the schedule asks "what's
already done?", it reads `wiki.processed`, not the disk.
When the extractor asks "which entities exist?", it reads `wiki.aliases`,
not file listings. This separation lets the workflow be retry-idempotent
without depending on filesystem state being perfectly consistent with PG.

**Atomicity is per-system.** Inside `commit`, all PG writes (`wiki.pages` +
`wiki.aliases` + `wiki.page_sources` + `wiki.processed`) are one transaction —
either all land or none do. Disk writes are atomic per-file (tmp + os.replace). PG and disk
together are *not* atomic — if PG commit fails after .md files are written,
the files are stranded but get rewritten on replay. The replay path is
write_page-idempotent for identical content.

## Failure model

The workflow doesn't propagate failures up to Dagster — it records them.

| Failure | Caught by | Recorded as |
|---|---|---|
| Extraction LLM error / PG read fails | `extract_entities` try/except | `wiki.processed.status='error'` with extract_error message |
| Single entity's synthesis fails | `process_entity` try/except | `wiki.processed.error` carries `"<entity_id>: <error>"`; status still `'ok'` if siblings succeeded |
| All entities fail | (same as above, in commit's status logic) | `wiki.processed.status='error'` |
| `commit` PG transaction fails | uncaught | Dagster sees the partition fail; LangGraph checkpoint preserves state; retry resumes from `commit` without re-running LLMs |

The "swallow into state" pattern is deliberate: a partial wiki-quality issue
shouldn't look like an infrastructure failure to Dagster. The escape hatch
for "I want to retry without checkpoint resumption" is documented in the
operations runbook (delete from `checkpoints` tables).

## Files

| File | Role |
|---|---|
| `runner.py` | Canonical entry point — compiles graph + checkpointer + Langfuse, invokes once per item |
| `graph.py` | Parent `StateGraph`, `WikiSynthesisState` TypedDict, fan-out logic |
| `nodes.py` | Parent nodes: `extract_entities`, `commit` |
| `entity_graph.py` | Sub-graph `StateGraph`, `EntityWorkflowState`, `process_entity` |
| `prompts.py` | Extraction + create + update prompt templates |
| `parsing.py` | Parse LLM page output, slug helpers, H2 preservation check |
