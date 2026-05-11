# `knowledge-evals`

Eval harnesses — does retrieval find the right document, does generation answer faithfully, does wiki synthesis preserve meaning. One harness category per question.

## Rules

- **No Dagster imports.** Evals run from a CLI or notebook, not from an asset graph. The workbench job that wraps an eval lives in `orchestrators/`.
- **Cross-package imports are allowed downward**: `domains` for `IngestItem`, `retrievers` for chunkers (`get_chunking_fn`) and chunk types. `workflows` is fair game when generation eval lands. Never depend on `orchestrators`.
- **External services are explicit.** OpenAI (embedding + judge) is a hard dep; the harness uses `tenacity` retry on transient failures only. Chroma is reached via `HttpClient` — caller starts the server, eval doesn't.
- **Datasets live with the package** (`packages/evals/datasets/`), not at repo root. They're part of the package contract; moving evals out of this repo someday should take the dataset along.
- **Results are local-only.** `data/eval_results/retrieval_<ts>.json` is gitignored — chronological on the laptop, never committed. Annotate dataset version (commit SHA of the JSONL) when comparing runs across regenerations.

## Layout

```
packages/evals/
├── README.md                     # this file
├── datasets/
│   ├── README.md                 # dataset cadence + Option 1 rationale
│   └── retrieval_eval.jsonl      # pinned eval pairs (Phase C v0 — 166 pairs)
├── src/evals/
│   ├── rag.py                    # legacy set-style metrics; workbench only
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
    source: str              # one of: raw_store | notes | sessions | research
    expected_content_id: str # = IngestItem.item_id; matched against chunks' content_id metadata

VALID_SOURCES = ("raw_store", "notes", "sessions", "research")
```

The same `content_id` lives on every chunk in Chroma (the runner sets it as metadata at upsert time). Retrieval is scored at **document granularity** — multiple chunks of the same doc all count as relevant for that query.

## Categories

| Submodule | Status | Scope | Entry point |
|---|---|---|---|
| `evals.retrieval` | ✅ active | Recall@K / MRR@K / nDCG@K for `(embedding_model, dims, chunker_per_source)` — does the right document come back for a query? | `uv run eval-retrieval` |
| `evals.generation` | ⬜ reserved | Faithfulness / answer-relevance / grounding for wiki + research answers. `ragas` is the planned harness, already in deps. | TBD |
| `evals.wiki` | ⬜ reserved | Wiki synthesis quality (entity merging, alias coverage, summary fidelity). Gold reference still undecided. | TBD |
| `evals.rag` (legacy) | 🟡 maintenance | Set-style `(retrieved_ids, expected_ids: list[str])` metrics. Used by the workbench `evaluate_retrieval_strategies` job; TODO at `rag.py:1` to consolidate with `retrieval.metrics`. | imported directly |

## The batch-cap trap (operational reality)

The harness has to embed the entire corpus per `(model, dims, chunker)` config — that breaks naive single-call batches in two places. Both fixed; documenting so the next contributor doesn't redo the same bug:

1. **OpenAI's `/embeddings` accepts ≤300k input tokens per request.** `OpenAIEmbedder.embed_batch` sub-batches at 250k tokens (4 chars/token estimate with headroom). The `_sub_batches` static method on the embedder is the implementation; do **not** call OpenAI with `input=texts` blindly on a real corpus (raw_store is ~2.4M tokens).
2. **Chroma's `collection.upsert` rejects batches above its server-side max (5461 default).** `_index_source` in `runner.py` loops the upsert in slices of 4000. Same anti-pattern, different layer.

If you add a new embedder or a new vector store, copy the sub-batch discipline — the corpus is too big for one-shot calls to either.

## Running `retrieval`

```bash
# Default: text-embedding-3-small @ 1536, current chunkers, eval-set auto-resolves
uv run --package knowledge-orchestrators --extra workbench eval-retrieval \
  --raw-store-db   "$BACKUP_SOURCE_DIR/raw_store.db" \
  --sessions-db    "$BACKUP_SOURCE_DIR/sessions.db" \
  --research-db    "$BACKUP_SOURCE_DIR/research.db" \
  --notes-dir      "$BACKUP_SOURCE_DIR/notes"

# Compare a candidate config — bigger embedder, swap session chunker
uv run eval-retrieval \
  --embedding-model text-embedding-3-large --embedding-dims 1536 \
  --chunker-sessions turn_grouping

# Partial run for iteration on one source
uv run eval-retrieval --raw-store-db "$BACKUP_SOURCE_DIR/raw_store.db" --limit 50
```

Requires:
- Chroma running on `localhost:8000` — `chroma run --path /tmp/chroma_eval --port 8000` (any persistence path; results don't outlive the run).
- `OPENAI_API_KEY` in env or `.env` (load via `python-dotenv` from notebook 02; CLI inherits from shell).
- At least one source path flag — sources without a path arg are silently skipped, handy for partial sweeps.

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

## Adding a category

The submodule shape (`types.py` → `dataset.py` → `runner.py` → `cli.py`) transfers. Generation eval is the next likely candidate:

1. Decide the dataset contract first — `{question, expected_answer, expected_context_ids}` is the shape `ragas` expects; that becomes a new dataclass in `evals/generation/types.py` paralleling `EvalPair`.
2. Lock the JSONL row in `evals/generation/dataset.py` with strict load + a new `VALID_SOURCES`-equivalent (or domain dimension, whatever the eval scores).
3. Add a `generation-eval` console script in `pyproject.toml` mirroring `eval-retrieval`.
4. Drop a sibling dataset folder at `packages/evals/datasets/generation_eval.jsonl`.

Don't shoehorn generation into `retrieval` — they ask different questions and need different gold data.

## Reference

- Retrieval dataset details: [`datasets/README.md`](datasets/README.md)
- Workspace overview: [`../../CLAUDE.md`](../../CLAUDE.md)
- Release history: [`../../CHANGELOG.md`](../../CHANGELOG.md)
