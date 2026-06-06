# `packages/evals/notebooks/`

Workbench notebooks for human-in-the-loop variant comparison + inspection of the extraction, workflows, and retrieval pipelines. Notebooks call into `evals/<pipeline>/workbench.py`; they don't implement it.

## Naming: `<mode>_<knob>__<scope>.ipynb`

Double underscore `__` separates knob from scope.

- **mode** — `ab` (variant compare) · `dump` (post-mortem) · `sweep` (multi-N rollup) · `extract` (build artefact)
- **knob** — what varies: `prompt` · `narrative` / `topic_card` / `followups` (the three extraction sub-prompts) · `topology` · `transform` · `chunker` · `embedder` · `dim` · `model`. Omit when mode covers it.
- **scope** — `content` (one document) · `bundle` (one synthesis source bundle) · `query` (one retrieval query) · `corpus` (across fixture set) · `run` (one stored RunRecord) · `page` (one wiki page)

## Cell structure — 9 fixed tagged cells

Every notebook starts from `_template.ipynb`. Cell order is fixed; each cell carries a single tag:

```
header → config → imports → load → adapter → fire → render → score → act
```

The `config` cell additionally carries the `parameters` tag (papermill convention — required for batch runs).

## Hard rules

1. **One `RESULTS` dict.** Declared in `config`, mutated by `fire`/`score`, read by `render`. No second mutable state pool.
2. **No hand-rolled extraction/synthesis.** Always route through `workflows.extraction.*` / `workflows.wiki_synthesis.*` / `retrievers.*`. Hand-rolled equivalents drift silently from production.
3. **Cache key.** `f"nb::{NOTEBOOK_STEM}::{variant}::{content_id}"` on every LLM call. Stable across cell re-runs.
4. **Outputs cleared before commit.** `Edit → Clear All Outputs`, save, commit. The jupytext `.py` carries source-of-truth for PR review.
5. **Promote shared helpers when ≥3 notebooks duplicate.** Earlier than that, copy. Don't preemptively abstract.

## Tooling

- `uv run poe jupyter` — JupyterLab on `localhost:8888`, rooted at this directory.
- `uv run poe nb-sync packages/evals/notebooks/<file>.ipynb` — push `.py` edits into the paired `.ipynb`.
- `uv run poe nb-run <input.ipynb> <output.ipynb> [-p key val …]` — papermill, `kp-eval` kernel.

**One-time per machine — register the kernel:**

```bash
uv run --extra notebooks python -m ipykernel install --user --name kp-eval --display-name "kp-eval (Python 3.13)"
```

**Per-notebook pairing — once when creating:**

```bash
uv run --extra notebooks jupytext --set-formats ipynb,py:percent packages/evals/notebooks/<file>.py
```

This produces the paired `.ipynb` alongside the `.py`. Both get committed.

## Available notebooks

Extraction (Step 3):
- `_template.ipynb` — starting point; copy and rename
- `ab_topic_card__content.ipynb` — A/B the topic-card sub-prompt on one content
- `ab_narrative__content.ipynb` — A/B the narrative sub-prompt on one content
- `ab_followups__content.ipynb` — A/B the followups sub-prompt on one content

Workflows + retrieval notebooks land in Steps 5 + 8.
