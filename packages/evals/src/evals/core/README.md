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
- `harness.py` — the shared variant → `FixtureRun` → score → stratified-aggregate → persist runner (`run_and_report`), plus `run_repeated` / `RepeatedReport` for N-run mean+range over a noisy judge. Only harnesses whose shape is `variant.run(fixture) -> FixtureRun` run through it (extraction, narrative coverage); retrieval and extract-claims keep their own run models.
- `manifest.py` — `RunManifest` (dataset/subject/model/judge/code-rev/mode/runs provenance envelope), `code_rev()` (git short sha), `format_manifest_line()`. Every eval entrypoint attaches one, whether or not it runs through `harness.py`.

## Not in scope

- The per-pipeline workbench / benchmark **wrappers** (`run_variants`, extraction's `run_benchmark`) — those live in per-pipeline subpackages and call into `harness.run_and_report` rather than reimplementing it.
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
    CostBudget, BudgetExceededError, CostEstimatorProtocol,
    # snapshotter.py
    snapshot,
    # runs.py
    save_run, load_run, run_dir,
    # diff.py
    DiffReport, diff_runs, render_diff_text, render_diff_html,
    # harness.py
    run_and_report, run_repeated, RepeatedReport,
    # manifest.py
    RunManifest, code_rev, format_manifest_line,
)
from evals.core.judges import JudgeProtocol, ExactMatchJudge, EmbeddingSimilarityJudge, LLMJudge
```
