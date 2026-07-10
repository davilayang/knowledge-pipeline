# `wiki_synthesis` workflow

A plain-function workflow that turns one source document into attributed wiki
updates. Extract-time Dagster assets in `fetch_extract_queue` call
`extract_claims` and `extract_entities` per item; `attributed_synthesis.synthesize_source`
resolves candidates against the live wiki and persists per-source attributed
claims; `promote_notes` (below) separately folds user-promoted notes in as
`derived` claims; `render_entity_pages` sweeps all entities and renders each
page from its accumulated attributed claims.

For operations (how to launch, retry, debug), see the attributed-lane runbook:
`packages/orchestrators/src/orchestrators/defs/fetch_extract_queue/README.md`.
This document is for engineers modifying the workflow.

## What the workflow does

Given a source document (an `IngestItem` — id, title, full text, source_type),
produce attributed updates to a structured wiki. The `PageType` literal
(`domains.wiki.types`) covers: `concept`, `tool`, `trend`, `person`,
`organization`, `method`, `dataset`, `other`. Entity extraction is
domain-agnostic with no hard cap — downstream attribution determines which
entities earn a page.

Each entity has exactly one wiki page on disk (`data/wiki/{slug}-{shortid}.md`
— flat under `data/wiki/`, no subdirectory; shortid = first 8 hex of the
opaque surrogate `entity_id`) and one row each in `entities` (identity
record) and `pages` (synthesised-artifact record). The same entity is
mentioned by many documents over time; `render_entity_pages` re-renders the
page from ALL attributed claims accumulated across sources.

## Semantic model

| Concept | Storage | Cardinality |
|---|---|---|
| Entity identity | `entities` row (entity_id PK, canonical_name, normalized_name, slug, entity_type) | 1 per real-world thing |
| Synthesised page | `pages` row (FK → entities) + flat `wiki/{slug}-{shortid}.md` | 1 per entity |
| Alias | `aliases` row (normalized_alias PK, entity_id FK) | many per entity (display form + variants) |
| Source contribution | attributed lane's `sources` / `claims` / `claim_entities` tables | `num_sources` derived on read via `attributed.count_sources_for_entity` |
| Co-occurrence link | none — derived from `claim_entities` at read time | drives the rendered `related` list (`co_count` = distinct shared sources) |
| Processed marker | `processed_items` row | 1 per (item_id, source_type) |

**Aliases** prevent duplicates. Before extraction, the workflow snapshots the
existing alias table (plus all entity canonical names) and hands it to the LLM
as YAML context. The extractor proposes a `title` and optional `matched_id`
(never a surrogate directly). `resolve_or_mint_batch` then applies the alias
gate: exact normalized_name / normalized_alias is authoritative → reuse; a
validated `matched_id` → reuse; fuzzy is advisory only (never auto-merged).
Two documents mentioning "Pandas" and "pandas-dev" update one page, not two.

**Provenance:**

- The rendered `num_sources` is **derived on read** via
  `attributed.count_sources_for_entity` over the attributed lane's
  `sources` / `claims` / `claim_entities` tables — no stored counter, so it's
  retry-safe and consistent by construction.
- `WikiPage.sources` (the LLM-echoed `[source_id]` in the synthesis
  output) is **ignored** for rendering — it's the single triggering item,
  not the accumulated set.

**Related links (derived, not stored):** the rendered `related` list is also
producer-authoritative and **derived on read** from `claim_entities` — two
entities co-occur when both are claimed within the same source. There is no edge
table: `get_related_for_entity` runs a self-join and ranks neighbours by
`co_count = COUNT(DISTINCT source_id)` (`co_count DESC, related_entity_id ASC`,
top-N). So `related` **accumulates across every article** that co-mentions an
entity, and — like `num_sources` — is consistent by construction: a re-extraction
that changes a source's claims is reflected on the next render with no edge upkeep.
`WikiPage.related` (this article's siblings) is no longer rendered; `pages.related_ids`
remains as a legacy this-tick advisory column. On a page *update*, producer-owned
frontmatter (`aliases`/`related`/`sources`/`num_sources`/`updated_at`) is stripped
from the existing page before it re-enters the synthesis prompt, so the LLM never
echoes accumulated metadata back into the body.

## State boundary: filesystem vs SQLite (wiki.db)

| Lives on disk (`data/wiki/`) | Lives in SQLite (`data/wiki.db`) |
|---|---|
| `{slug}-{shortid}.md` — the rendered page (flat, no subdirs) | `entities` — identity record (entity_id PK, canonical_name, normalized_name, slug, entity_type) |
| `index.md` — table of contents (regenerated) | `pages` — synthesised-artifact metadata (FK → entities); entity_type/slug/canonical_name read via join |
| `_index/resolve.json` — alias→entity_id resolution + per-entity orientation (`name`, `type`, `file`, `num_sources`, `page_hash`); newsletter-assistant bridge sidecar; written last so it never points past an `.md` file already on disk | |
| | `aliases` — entity name → id mapping (normalized_alias PK) |
| | `processed_items` — per (item_id, source_type) completion markers |

**Disk is the human-readable surface.** The .md files are what humans read,
diff in git, and reference. They're authored by the synthesis LLM, not
hand-edited.

**SQLite is the dedup/lineage truth.** When the schedule asks "what's
already done?", it reads `processed_items`, not the disk.
When the extractor asks "which entities exist?", it reads `aliases`,
not file listings. This separation lets the workflow be retry-idempotent
without depending on filesystem state being perfectly consistent with the DB.

**Atomicity is per-system.** All SQLite writes (`entities` + `pages` +
`aliases`) are one transaction — either all land or none do.
The `processed_items` row is written separately, after the graph commits and
the .md files are written. Disk writes are atomic per-file (tmp + os.replace).
SQLite and disk together are *not* atomic — but the crash-safe ordering (graph
→ files → processed_items) ensures a crash leaves a recoverable state: entities
are committed (retry reuses the same surrogates, no orphan files), and the item
stays un-processed so it re-queues and the write is retried.

## Failure model

The workflow doesn't propagate failures up to Dagster — it records them. The
"swallow into state" pattern is deliberate: a partial wiki-quality issue
shouldn't look like an infrastructure failure to Dagster. LLM or SQLite
failures record into `processed_items.status='error'`; uncaught SQLite
transaction failures bubble up so Dagster retries the partition from scratch —
there are no checkpoints to resume from.

## Attributed lane (Layer 1.5 → entity assignment)

The attributed lane works from per-source, extract-time outputs rather than raw
article spans: `extract_claims` distils one source into `[reported]`/`[opinion]`-tagged
claims, `extract_entities` extracts article-grounded candidates (both stored per
source), and `entity_assignment.assign_summary` resolves those candidates against
the LIVE wiki and maps each claim to the entity it is about — so the wiki can
*attribute* a claim ("a Medium piece claimed X") instead of asserting it.
A candidate unifies onto an existing entity surrogate instead of minting a
duplicate.

The extract-time producers (`extract_claims`, `extract_entities`) are wired as
Dagster assets (`fetch_extract_queue`); `assign_summary` consumes their stored
output via `assign_from_stored` in `attributed_synthesis.synthesize_source`, which
persists the result into `wiki.db` (`sources` / `claims` / `claim_entities`).
`render_entity_pages` (`attributed_synthesis`) re-renders each entity's page from
ALL its attributed claims as an unpartitioned sweep. The scoring harness lives in
`evals.wiki.claims` (gate + assignment diagnostics).

```
article-grounded candidates (extract_entities asset)  +  ClaimSet (extract_claims asset)
    │  resolve_or_mint_batch (LIVE wiki)
    ▼
entities + surface_forms    reuse an existing surrogate, else mint  (cross-path unification)
    ▼
per claim:  match_claim (deterministic surface-form) → mentioned entities = HINT
    │
    ├─ exactly 1 mention, no contrast cue, no rejected-entity name in text → unambiguous ─► entity_ids  (no LLM)
    └─ 0 or ≥2 mentions, or 1 mention + a contrast/dependency cue, or 1 mention + a
         rejected-entity name surfaces in the text (dropped candidate hid a co-mention) → ambiguous
         attribute_subjects_llm (ONE closed call over the whole claim list,
         each claim + its mention hint) → true subject(s) from the candidates
         (demote a passing co-mention; resolve a pronoun) ─────► entity_ids
    ▼
ClaimAssignment[]  +  salience over the claim texts (shared salience gate)
    │  group_by_entity
    ▼
EntityClaims[]  — per-entity attributed claim sets (salient vs co-mention)
```

The mention hint is deliberately not the assignment: attributing by mention
over-attributes a claim to every entity it names (e.g. "Microsoft will ditch
OpenAI" is about Microsoft, not OpenAI). Subject-attribution is CLOSED to the
candidate set — the model returns only extracted entities, never an invented
name or a descriptive phrase.

## Files

| File | Role |
|---|---|
| `extract_claims.py` | `extract_claims` — runs the extract-claims LLM call (gpt-4.1-mini, temperature=0) and returns a `ClaimSet` of `[reported]`/`[opinion]` tagged claims; content-shape-aware prior for spoken sources. Uses the shared-prefix layout (`extract_shared`) so its article read prompt-caches with `extract_entities` |
| `extract_entities.py` | `extract_entities` — article-grounded entity candidate extractor: reads the raw article + its claims, returns `Candidate`s (no cap; salience classifies the tail), behind a prompt that drops chrome / example-data / code identifiers. If the call's `finish_reason == "length"` (hit the output-token cap — a degeneration runaway, not a real long candidate list), the whole set is discarded and a warning logged rather than parsed. The attributed lane's candidate source — wired as its own extract-time Dagster asset (`deps=extract_claims`), and its stored candidates are consumed by `assign_summary` (via `assign_from_stored`). `render_candidates` is the canonical `Name — type` inverse of `parse_entity_candidates` for the stored round-trip |
| `extract_shared.py` | `shared_prefix_messages` — the `[system, article envelope, task]` message layout shared byte-identically by `extract_claims` + `extract_entities`, so the article is served from OpenAI's prefix cache on the second extract-time call |
| `entity_assignment.py` | Attributed lane (see above): `assign_summary` maps a summary's claims to wiki entities — drops any candidate whose normalized name is on the curator denylist (`rejected_entities`) before resolution (so a rejected entity can't re-mint or re-earn a page), then resolves the surviving candidates against the LIVE wiki → deterministic `match_claim` as a hint → closed `attribute_subjects_llm` over ambiguous claims (subject, not mention); a claim that surfaces a rejected-entity name is also routed to the ambiguous path (its hidden co-mention would otherwise fake a clean single-mention assignment); `group_by_entity` gives per-entity attributed claim sets with a salience flag. `assign_from_stored` is the storage bridge (parses the stored claims + candidate docs, then `assign_summary`) consumed by `attributed_synthesis.synthesize_source`. Persists nothing itself — persistence is in `attributed_persist` and `attributed_synthesis` |
| `prompts.py` | Prompt loader — resolves versioned `.md` files under `prompts/wiki/` via `KP_PROMPTS_ROOT`; exposes `EXTRACT_SHARED_SYSTEM`, `EXTRACT_ARTICLE_ENVELOPE`, `EXTRACT_CLAIMS_TASK`, `EXTRACT_ENTITIES_TASK`, `SUBJECT_ATTRIBUTION_SYSTEM`, `SUBJECT_ATTRIBUTION_USER` |
| `parsing.py` | Parse LLM page output, slug helpers, H2 preservation check |
| `attributed_persist.py` | `persist_source_assignment` — writes one source's attributed claims into wiki.db in the caller's transaction: upserts the source row, inserts minted entities, inserts each claim and its claim→entity links. Idempotent (ON CONFLICT DO NOTHING). Returns the surviving source_id |
| `attributed_synthesis.py` | Orchestration layer the Dagster assets call: `build_source_record` (queue_items row → `SourceRecord`), `synthesize_source` (runs `assign_from_stored` then `persist_source_assignment` in one transaction; returns source_id), `render_entity_pages` (unpartitioned sweep — renders every entity's attributed page from ALL its `wiki.db` attributed claims, skipping those below the ≥2 claims OR ≥2 sources floor) |
| `promote_notes.py` | `promote_notes` — reads every `promote: true` note (`domains.notes.promoted.read_promoted_notes`), resolves its `entities` hints against the live wiki in one batch (`resolve_or_mint_batch`; curator-denylisted hints dropped first), and writes each note as a note-origin source (`content_key = local:{note_id}`) with ONE `derived` claim linked to its resolved entities — REPLACE semantics per note (prior claim deleted before re-insert) and reconciling (a note-origin source whose note is no longer promoted is deleted, cascading its claim). Returns a `PromoteResult` (`written` / `changed` / `removed` / `fuzzy_hints`); `.dirty` (`changed + removed`) is the render-trigger signal so an unchanged standing note doesn't force a re-render |
