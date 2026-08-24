# `knowledge-evals` — style guide

Cross-surface conventions for the harnesses under `src/evals/<category>/`, so a
later session extends or cleans one up consistently instead of re-deriving the
pattern. The smallest set of rules that prevents the next divergence — not a
handbook. **Diverge only with a stated reason in the category's own README.**

The package-wide substrate rules — no-provider-in-`core`, no-Dagster,
datasets-live-with-the-package, two-tier results (local JSON detail + Notion
headline), mean-of-≥3-runs for noisy judges — live in [`README.md`](README.md)
and are not repeated here. The sibling `newsletter-assistant` repo keeps a
parallel eval style guide; the shared vocabulary keeps cross-KOS eval work
reading the same.

## The run-layer — one runner, one provenance envelope

Two shared pieces in `evals/core/`, used at different scopes:

- **`run_and_report` / `run_repeated`** (`core/harness.py`) fit the
  `variant.run(fixture) -> FixtureRun -> score` shape. **Only the extraction /
  narrative-coverage harnesses run through them** — that shape maps onto them
  directly. The retrieval (index-then-query) and extract-claims (source-level
  aggregation) harnesses keep their own run models; forcing them onto
  `Variant`/`RunRecord` buys conformity, not leverage. Don't.
- **`RunManifest`** (`core/manifest.py`) is the one thing *every* real eval
  entrypoint shares — a provenance envelope (dataset + schema, subject,
  subject/judge models, code rev, gate-vs-report mode, N runs) persisted or
  printed with the run. It is a **provenance standard, not a unified run
  model**: it answers "what produced this score," nothing more. Build one in
  each entrypoint (`code_rev()` fills the sha; `format_manifest_line` prints it).

## The contract — one rule per axis

| Axis | Rule |
|---|---|
| **Substrate** | Pure, provider-free, JSON-safe primitives (typed records, fixture load/save, judge skeletons, run persistence, manifest, runner) live in `evals/core/` and are consumed downward. A harness never re-implements a substrate primitive — reuse the `core` type or extend it there. |
| **Datasets** | Pinned gold is git-tracked in `packages/evals/datasets/` (one home) with a `schema_version` header row. Scored-run outputs stay gitignored under `data/eval_runs/` + `data/eval_results/` — see [`README.md`](README.md). |
| **Provenance** | Every real eval entrypoint attaches a `RunManifest` — persisted into its result JSON (retrieval) or printed with its report (claims, coverage). Don't scatter dataset/model/judge/rev across ad-hoc prints; put them in the manifest. |
| **Subject seam** | Score the *real* production seam — build the shipped extractor/workflow, load the shipped prompt file (with `strip_design_notes`, matching prod), import the shipped scorer. Local per-item assembly is fine; **forking the prompt body into the eval is not**. The test: if the prod prompt changes, does the eval follow automatically? |
| **Determinism** | Deterministic scorers are **pure — no network**. Isolate every API call in one clearly-named function and keep scoring / merge / aggregate / report pure so they unit-test offline with stubbed `embed_fn` / `chat_fn`. The anti-spaghetti rule. |
| **Judge / jury** | LLM judge **only for genuinely semantic axes** (faithfulness, coverage, distortion, on-topic); mechanical axes stay deterministic. A judge from the subject's own model family self-prefers — prefer a cross-family judge, and record the coupling in `RunManifest.judge_model` so a reader can see it. **Reach the cross-family judge via a local CLI agent subprocess (`claude -p`, `codex exec`) before an HTTP provider API** — it needs no extra key, is cross-family by construction against an OpenAI-extracted subject, and keeps cost off the eval; a provider API is the fallback when the CLI isn't installed in the run env. Three things the subprocess does not give you and the caller must supply: record the **resolved** model id in `judge_model`, since a CLI default that upgrades between runs invalidates a trend line with no signal in the manifest; **enforce the response shape yourself**, since there is no `response_format={"type":"json_object"}` and a parse failure that degrades to an empty verdict set scores as *absent* — a fabricated regression rather than a loud error; and pin sampling where the CLI allows it, since an uncontrolled-temperature judge adds the variance the mean-of-≥3 rule exists to tame. Keep the call behind an injected `judge_fn` so subprocess vs API is swappable and stub-testable offline. |
| **Run mode** | A *gate* asserts a committed floor (`RunManifest.mode="gate"`), runnable as a `poe` task / console script (env-skips without credentials). A *report* emits rates and gates nothing (`mode="report"`) — valid for a noisy LLM-judge metric where no merge gate is wanted. Say which in the README; don't force a gate for symmetry. |

## Role + README — a hint, not a mandate

A surface is usually a **decision race** (choosing between candidate configs —
arms, `--baseline`, before/after diffs), a **regression floor** (one shipped
subject vs a committed baseline), or **calibration tooling** (checking a judge
against hand labels). A surface can be more than one at once — don't contort it
to fit one label. When a decision race graduates, **collapse** to the shipped
subject or **explicitly label** the leftover arms as historical; never leave a
finished race's multi-arm shape sitting unlabelled.

A category gets a `README.md` **when its run/gate semantics aren't obvious from
the code** — stating role, subject, axes (deterministic vs judge-scored), gate
vs report, and the exact run command. Not every category needs one.

## Building the golden set

For **dataset-construction rigor** — sampling frame, strata, judge calibration,
statistical power — use the `playbook-gold-eval` skill rather than re-deriving
it here.
