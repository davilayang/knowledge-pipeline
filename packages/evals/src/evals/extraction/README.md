# `evals/extraction/`

First per-pipeline harness consuming `evals/core/`. Wraps `workflows.extraction.ThreeCallOpenAIExtractor` into a `Variant` so prompt swaps + content transforms can be A/B tested in a notebook or scored over a fixture set via `run_benchmark` (the thin extraction wrapper over `evals.core.run_and_report`).

## Three scored surfaces

| What it scores | Scorer | How it's run |
|---|---|---|
| **Topic Card** field quality vs a v5 baseline | `TopicCardScorer` | workbench notebooks (`run_variants`) / `run_benchmark` — no CLI |
| **Narrative coverage** — does `narrative_md` cover the gold follow-up threads? | `NarrativeCoverageScorer` | `eval-narrative-coverage` CLI |
| **Narrative fidelity** — omission / corruption / invention vs gold threads | `NarrativeFidelityScorer` (`evals/extraction/scorers.py`; metric substrate in `evals.extraction.fidelity`) | seed stage — scorer + gold exist, not wired into a CLI or `evals.extraction.__init__` exports yet |

### Re-running narrative coverage

When the narrative prompt (or model) changes, re-score it against the pinned gold — needs `OPENAI_API_KEY` (extraction + present/absent judge both call OpenAI):

```bash
set -a && source .env && set +a && \
  uv run eval-narrative-coverage --narrative narrative_v3 --runs 3
```

- `--narrative <label>` — candidate prompt (default `narrative_v3`); `--baseline <label>` — a second prompt to diff against in the same pass (prints the per-content-type and per-shape Δ and flags any `← REGRESSION`).
- **Comparing two narrative prompts in one pass is no longer possible: the extractor generates the field list from `domains.extraction.schemas.Narrative`, so only a prompt written against the current shape can run. To diff against a prior prompt, run this from a checkout of the release that carried it and compare the recorded means.**
- `--runs N` (default 3) — the LLM judge is noisy at n=7; the CLI reports the **mean + observed range** over N full re-runs. `--dry-run` estimates cost first.
- Both prompts are loaded with design-notes headers stripped (`strip_design_notes`, matches prod). Output ends with **Notion-ready rows** — log the mean to the Eval Runs DB (see [`../../../README.md`](../../../README.md) → "Results are two-tier"). Detailed per-run JSON persists under `data/eval_runs/` (gitignored).
- The gold lives at [`datasets/narrative_coverage_gold.jsonl`](../../../datasets/README.md#narrative_coverage_goldjsonl); the single-fixture hit/miss inspector is the `ab_narrative_coverage__content` workbench notebook.
- The gold header's `gold_version` (the data revision) rides along the whole way: `_fixtures()` reads it off `FixtureHeader.extra` and returns `(fixtures, gold_version)`, it lands in the run's `RunManifest.dataset_version`, and the printed per-arm row shows it as `Dataset=<gold filename>@v<gold_version>`.

### Narrative fidelity (seed, no CLI yet)

`NarrativeFidelityScorer` (`evals/extraction/scorers.py`) scores the narrative's `narrative_md`
against gold threads for omission (`faithful_recall`), corruption (`distortion_rate`), and
invention (`fabrication_rate`), via two single-purpose injected judges — a fidelity judge
(`DEFAULT_FIDELITY_PROMPT`, per-gold-thread faithful/distorted/absent) and a fabrication judge
(`DEFAULT_FABRICATION_PROMPT`, per-produced-thread invented bool). `evals.extraction.fidelity`
also carries `merge_fidelity_verdicts` / `merge_invented` — conservative (false-pass-averse)
two-juror aggregation for scoring the same axis with two backends — but the scorer doesn't call
them; only the metric functions (`faithful_recall`, `distortion_rate`, `fabrication_rate`,
`severe_omission_count`) are wired in today. The gold lives at
[`datasets/narrative_fidelity_gold_seed.jsonl`](../../../datasets/README.md#narrative_fidelity_gold_seedjsonl)
(11 fixtures). This is substrate only — no `eval-*` CLI entry point and not yet re-exported from
`evals.extraction.__init__` — until a runner lands.

## Public API

See `evals/extraction/__init__.py` for the re-export list.

## Scorer mix

| Topic Card field | Scorer |
|---|---|
| `extracted_title`, `core_mechanism`, `transferable_pattern` | EmbeddingSimilarityJudge (OpenAI text-embedding-3-small by default) |
| `best_example`, `main_tension`, `candidate_tie_backs` | LLMJudge (gpt-4o-2024-11-20 by default) |

`extracted_title` was originally on exact-match but every real run scored 0.0 — LLMs basically never reproduce hand-written reference titles verbatim. Moved to embedding similarity after the first benchmark surfaced the issue.

Scorers carry no provider dependency — callers inject `embed_fn` and `chat_fn`. Tests pass stubs; runtime callers wire OpenAI clients.

## Scope notes

- Fixtures pair raw content with a **v5 baseline expected output** (production extractor as of 2026-06-06). The baseline is a **regression anchor, not human-curated gold**.
- **Measurement floor:** the scorer surfaces *change vs v5*, not *absolute quality*. A +0.03 overall bump tells you a variant produces v5-shaped output better; it does NOT tell you the variant is closer to ground truth. Improvements along dimensions v5 already optimised (brevity in `extracted_title`, narrative density in `core_mechanism`) are detectable; improvements along dimensions v5 *doesn't* optimise are systematically under-credited. Treat scores as direction signals, not absolute rankings, until human-curated gold lands post-Step 6.
- First cut: 5 fixtures × 3 content types = 15. Refresh to 15/type after `eval-corpus` tooling lands (spec Step 6).
