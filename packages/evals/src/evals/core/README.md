# `evals/core/`

Pure-function substrate for the eval / workbench layer. Consumed by per-pipeline harnesses (`evals/extraction/`, `evals/workflows/`, `evals/retrieval/`).

## In scope

- Typed records (Variant, FixtureRun, RunRecord, ScoreReport, StageTrace, …) — all `frozen=True` dataclasses, JSON-serialisable.
- Fixture JSONL load/save with `schema_version` header validation.
- Run persistence under `data/eval_runs/{kind}/{target}/{version}/{run_id}/` — JSON layout mirrors Inspect AI's `.eval` shape for forward-compat.
- `CostBudget` + estimator protocol + concurrency cap.
- LangGraph-state-friendly `snapshot()` that emits `{"__skipped__": "<type>"}` sentinels for non-serialisable fields.
- Judge skeletons (exact, embedding, LLM) with **injected** callables — no provider deps here.
- Per-field diff renderer (text + HTML).

## Not in scope

- The workbench / benchmark surfaces (`run_variants`, `run_benchmark`) — live in per-pipeline subpackages.
- Real OpenAI calls — tests pass mocks; runtime callers wire in real `embed_fn` / `chat_fn`.
- Cross-pipeline composition — Variants are per-pipeline; cross-pipeline is a deferred design question.

## Public API

```python
from evals.core import (
    # types.py
    RunStatus, VariantProvenance, FixtureRun, RunRecord,
    ScoreReport, FixtureRef, FieldScore, StageTrace,
    # variants.py
    Variant, RetrievalVariant, variant_identity, corpus_signature,
    # fixtures.py
    FixtureHeader, SchemaVersionMismatch, load_fixtures, save_fixtures,
    # cost.py
    CostBudget, BudgetExceededError,
    # snapshotter.py
    snapshot,
    # runs.py
    save_run, load_run, run_dir,
    # diff.py
    DiffReport, diff_runs, render_diff_text, render_diff_html,
)
from evals.core.judges import JudgeProtocol, ExactMatchJudge, EmbeddingSimilarityJudge, LLMJudge
```
