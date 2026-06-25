# `knowledge-evals`

Eval substrate (`src/evals/core/`) + per-pipeline harnesses. Substrate primitives are pure-function (frozen dataclasses, schema-versioned fixtures, JSON-safe snapshots, judge skeletons with injected callables). Per-pipeline harnesses (`src/evals/extraction/`, `src/evals/workflows/`, `src/evals/retrieval/`) compose substrate primitives into runnable workbenches + benchmark CLIs.

## Rules

- **No Dagster imports.** Evals run from a CLI or notebook, not from an asset graph.
- **No provider imports in `evals/core/`.** Judges take injected `embed_fn` / `chat_fn` callables; the substrate stays provider-agnostic. Production wires `retrievers.embedding.OpenAIEmbedder` or `workflows.llm` callables in per-pipeline subpackages.
- **Cross-package imports are allowed downward**: `domains` for `IngestItem`, `retrievers` for chunkers (`get_chunking_fn`) and chunk types, `workflows` for LLM-calling primitives. Never depend on `orchestrators`.
- **External services are explicit.** OpenAI (embedding + judge) is a hard dep in the harness layer, never in the substrate; harnesses use `tenacity` retry on transient failures only. Chroma is reached via `HttpClient` — caller starts the server, eval doesn't.
- **Datasets live with the package** (`packages/evals/datasets/`), not at repo root. They're part of the package contract; moving evals out of this repo someday should take the dataset along.
- **Results are local-only.** `data/eval_runs/{kind}/{target}/{version}/{run_id}/` is gitignored — chronological on the laptop, never committed. `workbench` runs auto-prune at 30d via `uv run poe eval-cleanup-workbench`; `benchmark` runs retained indefinitely (trend data); `promoted/` excluded from cleanup for manual preservation.

## Layout

```
packages/evals/
├── README.md                     # this file
├── datasets/
│   ├── README.md                 # dataset cadence + Option 1 rationale
│   └── retrieval_eval.jsonl      # pinned eval pairs (Phase C v0 — 166 pairs)
├── src/evals/
│   ├── core/                     # ✅ pure-function substrate (no LLM, no Dagster, no Postgres)
│   │   ├── types.py              # RunStatus, FixtureRun, RunRecord, ScoreReport, StageTrace, ...
│   │   ├── variants.py           # Variant, RetrievalVariant, variant_identity, corpus_signature
│   │   ├── fixtures.py           # FixtureHeader, schema-versioned JSONL load/save
│   │   ├── cost.py               # CostBudget, BudgetExceededError, CostEstimatorProtocol
│   │   ├── snapshotter.py        # snapshot() — JSON-safe with {"__skipped__": "<type>"} sentinels
│   │   ├── runs.py               # save_run/load_run/run_dir — Inspect-AI-shaped per-run JSON
│   │   ├── diff.py               # DiffReport + text/HTML renderers
│   │   └── judges/               # ExactMatchJudge, EmbeddingSimilarityJudge, LLMJudge (injected callables)
│   └── retrieval/                # ✅ active retrieval eval harness
│       ├── types.py              # EvalPair, EvalConfig, SourceMetrics, EvalRunResult
│       ├── dataset.py            # load_eval_set, group_by_source (strict JSONL parse)
│       ├── embedder.py           # Embedder Protocol + OpenAIEmbedder + Fake
│       ├── cache.py              # disk-backed embedding cache
│       ├── metrics.py            # hit_at_k / mrr_at_k / ndcg_at_k
│       ├── runner.py             # index → query → metrics orchestration
│       └── cli.py                # eval-retrieval console script
└── pyproject.toml
```

## Contract — `EvalPair` (the JSONL row)

Every eval pair is one JSONL line of `{query, source, expected_content_id}`. `load_eval_set` is **strict** — missing keys or unknown sources raise `ValueError` so eval runs don't silently skip rows.

```python
@dataclass(frozen=True)
class EvalPair:
    query: str
    source: str              # one of: raw_store | notes | sessions | wiki
    expected_content_id: str # = IngestItem.item_id (= wiki entity_id for the wiki source); matched against chunks' content_id metadata

VALID_SOURCES = ("raw_store", "notes", "sessions", "wiki")
```

The same `content_id` lives on every chunk in Chroma (the runner sets it as metadata at upsert time). Retrieval is scored at **document granularity** — multiple chunks of the same doc all count as relevant for that query.

## Categories

| Submodule | Status | Scope | Entry point |
|---|---|---|---|
| `evals.core` | ✅ active | Pure-function substrate — `Variant` + `variant_identity` + schema-versioned fixtures + `RunRecord` persistence + injected-callable judges. Provider-agnostic. | (imported by harnesses) |
| `evals.retrieval` | ✅ active | Recall@K / MRR@K / nDCG@K for `(embedding_model, dims, chunker_per_source)` — does the right document come back for a query? | `uv run eval-retrieval` |
| `evals.extraction` | ✅ active | Topic Card field scoring with variant comparison + per-content-type stratification. Composes `workflows.extraction.ThreeCallOpenAIExtractor` via injected callables. | `uv run eval-extraction` |
| `evals.workflows` | ⬜ pending (Step 5; Step 4 prereq) | Wiki synthesis quality via per-node `StageTrace`. Requires `wiki_synthesis` decomposed into node factories first (Step 4). | `uv run eval-workflows` |

## Substrate primitives + composition patterns

Substrate public API (see `src/evals/core/README.md` for full scope):

```python
from evals.core import (
    Variant, RetrievalVariant, variant_identity, corpus_signature,
    FixtureHeader, SchemaVersionMismatch, load_fixtures, save_fixtures,
    CostBudget, BudgetExceededError,
    RunRecord, FixtureRun, RunStatus, ScoreReport, FieldScore, StageTrace,
    VariantProvenance, FixtureRef,
    snapshot, save_run, load_run, run_dir,
    DiffReport, diff_runs, render_diff_text, render_diff_html,
)
from evals.core.judges import (
    JudgeProtocol, ExactMatchJudge, EmbeddingSimilarityJudge, LLMJudge,
)
```

Three invariants the substrate enforces:

1. **Identity over (config, provenance).** `variant_identity()` ignores display `name` and the `run` callable; hashes `config` + asdict'd provenance. Validates JSON-safety up-front — sets, `Path`, custom objects raise `ValueError` at construction time so cache keys can't silently drift across processes.
2. **Schema-versioned fixtures.** `load_fixtures(path, expected_schema_version=N)` raises `SchemaVersionMismatch` on missing header or unexpected version. No silent degradation across schema bumps.
3. **JSON-safe snapshots.** `snapshot()` recurses dicts/lists/tuples, unfolds dataclasses, sentinels everything else as `{"__skipped__": "<typename>"}`. Use it on LangGraph state before persisting to `StageTrace`.

Four composition patterns recur. Each names the load-bearing primitives + when to bump `output_schema_version`.

### Pattern 1 — Adding a new content type

**Example:** podcast extraction (10k–50k token transcripts, multi-topic, speaker attribution).

- **Prompts:** add new files in `prompts/extraction/` OR add a content-type branch inside existing per-role prompts (`[content_type: Podcast]`).
- **Fixtures:** `FixtureHeader.extra["content_type_stratification"]` absorbs the new type without a `schema_version` bump *if* row shape (`fixture_id` + `expected_*`) holds. New row fields → bump `schema_version`.
- **Variants:** put content-type-specific knobs in `Variant.config` (e.g. `use_chapter_markers: True`). Each knob hashes into identity.
- **Output schema:** bump `VariantProvenance.output_schema_version` if the new type emits a different artefact shape (multi-topic → `topics: list[TopicCard]`). Single-topic types keep schema=1.
- **Headline output:** `diff_runs` as a qualitative survey — read whether the new content type produces useful Topic Cards.

### Pattern 2 — Upgrading an existing prompt in place

**Example:** arxiv prompt targeting math notation preservation + first-author attribution + tie-backs as citations.

- **Prompts:** edit the existing per-role markdown OR add an `<content_type>_overrides` config field consumed by the prompt body's content-type branch.
- **Fixtures:** reuse — schema doesn't change.
- **Variants:** one new config field captures the upgrade (`arxiv_overrides: {preserve_math_notation: True, ...}`). Identity flips; cache invalidates.
- **Output schema:** stays at 1. Upgrade keeps the same Topic Card shape.
- **Headline output:** `ScoreReport.stratifications.by_failure_mode` — map each prompt directive to a regex/heuristic check. Direct mapping from "what the prompt tries to fix" → "what the scorer measures."
- **Promotion gate:** benchmark CLI `--gate` mode fails CI if any `by_failure_mode` metric regresses or any targeted metric falls below threshold.

### Pattern 3 — Composing a workflow graph

**Example:** wiki synthesis dedup — insert a `merge_alias_variants` node between `extract_entities` and the fan-out so duplicate entities (e.g. `transformer` vs `transformers`) share one synthesis call.

- **Prerequisite:** `wiki_synthesis` must be decomposed into node factories first (Step 4 of the refactor). Until then, "the graph" is hard-coded edges inside `build_graph()` and variant identity for graph composition is a lie — flipping a `graph_id` string label maps to a code revision, not a config choice.
- **Variants:** post-decomposition, `Variant.config["graph_nodes"]` is an ordered `list[str]`. Substrate validates JSON-safety; list[str] is allowed.
- **Output schema:** stays at 1 — wiki pages are markdown files regardless of graph topology.
- **Headline output:** **`StageTrace` per node.** Diff on final outputs ("4 pages → 3 pages") doesn't tell you *where* the change landed; stage-list comparison shows `inserted: {'merge_alias_variants'}` `removed: {'synthesize_page[concept__transformers]'}`. `snapshot()`'s sentinel pattern earns its keep — `AliasStore` + `psycopg.Connection` in state become `{"__skipped__": "<typename>"}` without crashing the trace.
- **Cost lens:** `ScoreReport.stratifications.by_node_cost_usd` — measures whether the new node pays for itself per LLM call avoided.

### Pattern 4 — Migrating an implementation (golden regression)

**Example:** `ThreeCallOpenAIExtractor` → `LangGraphExtractor`. Different implementation, **same Topic Card output expected**.

- **Variants:** `Variant.config["extractor"]` flips between implementation classes. Provenance carries any new node prompts (`planner_v1`, `critic_v1`) distinctly.
- **Output schema:** **MUST stay identical on both variants.** Type-level encoding of the migration contract — if a reviewer bumps the candidate's `output_schema_version`, the contract has been declaratively violated.
- **Headline output:** **`diff_runs` becomes a GATE, not a survey.** Pass the contract field set as `field_picker`; any diff is a regression to investigate.
- **Cost lens:** Per-node `StageTrace` + `output_snapshot["cached_tokens"]` exposes prefix-cache regressions. LangGraph migrations risk breaking the OpenAI prefix cache if per-node system prompts differ from the current shared prompt prefix.
- **Promotion gates:**

   | Gate | Threshold |
   |---|---|
   | Contract field diffs | = 0 on stratified gold set |
   | Cache hit rate | candidate ≥ 0.6 × current |
   | Cost overhead | ≤ 20% (above requires quality justification via LLM-judge dimension) |
   | Production parity (7-day shadow) | < 5% partition drift → else auto-rollback |

## The batch-cap trap (operational reality)

The harness has to embed the entire corpus per `(model, dims, chunker)` config — that breaks naive single-call batches in two places. Both fixed; documenting so the next contributor doesn't redo the same bug:

1. **OpenAI's `/embeddings` accepts ≤300k input tokens per request.** `OpenAIEmbedder.embed_batch` sub-batches at 250k tokens (4 chars/token estimate with headroom). The `_sub_batches` static method on the embedder is the implementation; do **not** call OpenAI with `input=texts` blindly on a real corpus (raw_store is ~2.4M tokens).
2. **Chroma's `collection.upsert` rejects batches above its server-side max (5461 default).** `_index_source` in `runner.py` loops the upsert in slices of 4000. Same anti-pattern, different layer.

If you add a new embedder or a new vector store, copy the sub-batch discipline — the corpus is too big for one-shot calls to either.

## Running `retrieval`

```bash
# Default: text-embedding-3-small @ 1536, current chunkers, eval-set auto-resolves
uv run eval-retrieval \
  --raw-store-db   "$BACKUP_SRC_DIR/raw_store.db" \
  --sessions-db    "$BACKUP_SRC_DIR/sessions.db" \
  --notes-dir      "$BACKUP_SRC_DIR/notes" \
  --wiki-dir       data/wiki

# Compare a candidate config — bigger embedder, swap session chunker
uv run eval-retrieval \
  --embedding-model text-embedding-3-large --embedding-dims 1536 \
  --chunker-sessions turn_grouping

# Partial run for iteration on one source
uv run eval-retrieval --raw-store-db "$BACKUP_SRC_DIR/raw_store.db" --limit 50
```

Requires:
- Chroma running on `localhost:8000` — `chroma run --path /tmp/chroma_eval --port 8000` (any persistence path; results don't outlive the run).
- `OPENAI_API_KEY` in env or `.env` (load via `python-dotenv` from notebook 02; CLI inherits from shell).
- At least one source path flag (`--raw-store-db`, `--sessions-db`, `--notes-dir`, `--wiki-dir`) — sources without a path arg are silently skipped, handy for partial sweeps.

Output: per-source table to stdout, `data/eval_results/retrieval_<timestamp>.json` for the diff trail.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `openai.OpenAIError: api_key client option must be set` | `OPENAI_API_KEY` not in env — `set -a; source .env; set +a` first |
| `openai.BadRequestError: Requested N tokens, max 300000` | embedder's sub-batch broken; check `OpenAIEmbedder._sub_batches` (see "batch-cap trap" above) |
| `ValueError: Batch size N exceeds maximum 5461` | runner's upsert chunking broken; check the `BATCH = 4000` loop in `_index_source` |
| `httpx.ConnectError: connect call failed ('localhost', 8000)` | Chroma not running — start it (`chroma run --path ... --port 8000`) |
| `FileNotFoundError: ... retrieval_eval.jsonl` | running from wrong cwd — CLI's default `--eval-set` is repo-root-relative; either `cd` to repo root or pass `--eval-set` explicitly |
| `no such table: contents` | wrong `--raw-store-db` path — pointed at an empty or unrelated `.db` |

## Adding a per-pipeline harness

The submodule shape (`types.py` → `variants.py` → `workbench.py` → `benchmark.py` → `scorers.py`) is the substrate-aligned form. Each new harness composes `evals.core` primitives:

1. **Pick the composition pattern** above. Most extraction work is Pattern 1 or 2; wiki work is Pattern 3 (and needs the Step 4 node-factory decomposition first); implementation migrations are Pattern 4.
2. **Decide the fixture contract first** — define `<Target>Fixture` in `evals/<target>/types.py` with the row shape (`fixture_id` + `expected_*` fields). Pin `schema_version` for the JSONL header.
3. **Build variant constructors** in `evals/<target>/variants.py` — small builder functions that return `Variant(config=..., provenance=..., run=...)`. The `run` callable wraps the actual extractor / LangGraph workflow / retrieval index.
4. **Add a `workbench.py`** with notebook-friendly helpers (`run_variant`, `run_variants`, render-results-as-HTML).
5. **Add a `benchmark.py`** with a CLI entrypoint that takes a variant label + fixture set + budget and returns a `RunRecord` with scored aggregation. Console script in `pyproject.toml`.
6. **Drop a sibling dataset** at `packages/evals/datasets/<target>_eval.jsonl` with a schema-versioned header.

For pre-substrate harnesses (`evals.retrieval` predates `evals.core`), the legacy submodule shape (`types.py` → `dataset.py` → `runner.py` → `cli.py`) still works; Step 8 lifts it to the substrate-aligned form by adding `variants.py` + a `RetrievalVariant`-aware `benchmark.py` (was: `runner.py`).

## Reference

- Retrieval dataset details: [`datasets/README.md`](datasets/README.md)
- Workspace overview: [`../../CLAUDE.md`](../../CLAUDE.md)
- Release history: [`../../CHANGELOG.md`](../../CHANGELOG.md)
