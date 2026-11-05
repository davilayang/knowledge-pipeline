# Extraction datasets

Read by `evals.extraction`. Section conventions: [`../README.md`](../README.md#what-a-dataset-section-records).

## `extraction_eval.jsonl`

JSONL of `{fixture_id, content_type, content, expected_topic_card}` rows consumed by the `evals.extraction` harness — `run_benchmark` (scored aggregation over `evals.core.run_and_report`) and the workbench notebooks under `packages/evals/notebooks/ab_*__content.ipynb` (single-fixture eyeball). Schema validated by `evals.core.load_fixtures(expected_schema_version=1)` — bad rows fail loudly.

### v0 (2026-06-06) — Step 3 synthetic first-cut

| content_type | Fixtures | Topic neighbourhood | Difficulty mix |
|---|---|---|---|
| Article | 5 | Memory science (spaced repetition), ML history (EM/Minsky), data infra (text dedup, DuckDB optimisation), energy (Oklo microreactors) | Easy → Hard mix; named orgs & numbers throughout |
| arXiv | 5 | Fluid dynamics ML, streaming SGD, protein diffusion, adaptive PDE stencils, continuous-time RL | Abstracts only; varied subfields to avoid LLM-research monoculture |
| YouTube | 5 | Acting craft (Gadot/Lex), ML scaling laws, hippocampal neuroscience, UK grid forecasting, paleontology | Talk/podcast openings; concrete named studies |

**Total: 15 fixtures**, balanced across stratification dimensions.

### How they were curated

- **Drafted by `codex-advisor`** with the v5 topic_card prompt as context and explicit instructions on per-field grammatical rules (NAMED-METHOD does VERB to OUTCOME for `core_mechanism`; named org/person/paper/number in `best_example`; attributed-concrete hooks in `candidate_tie_backs`).
- **Reviewed inline** on the way in. Two typos corrected (`Googd's` → `Google's`; `globallyunpredictable` → `globally unpredictable`).
- **One historically dubious framing kept on purpose** (`art_002`: Minsky+EM credit). Useful for testing whether the extractor faithfully reproduces source claims rather than correcting them.
- **No live LLM calls.** Content and expected_topic_card are both hand-crafted text.

### Measurement floor (load-bearing caveat)

`expected_topic_card` rows are **hand-written examples of what v5-shaped output *should* look like — they are NOT actual v5 extractor outputs.** Scoring a candidate prompt against this set tells you *"does the candidate produce output that looks like what I think v5 should produce"* — it does NOT tell you *"does the candidate match what v5 actually produces"*. Score deltas in this v0 are direction signals, not absolute rankings.

Real v5-baseline curation (run the production three-call extractor against 15 real raw_store rows, pair each with its output as `expected_topic_card`) lands once `eval-corpus` tooling exists (spec Step 6). Until then, treat absolute scores from this set as advisory; trust ordinal comparisons between variants more than absolute numbers.

### Known v0 gaps

- **Synthetic content.** Real raw_store content has structural quirks (truncated transcripts, embedded HTML, mixed languages) that this set won't surface. v1 from raw_store will.
- **No second_example field.** The `TopicCard` schema has an optional `second_example` field; fixtures don't include it. Scorer ignores it anyway, but a refresh should consider populating it.
- **No deliberately-broken fixtures.** No fixtures specifically test extractor robustness to short / repetitive / off-domain content. Add a `synthetic_edge_cases/` slice in v1 if extractor failure modes become a focus.

### Adding new fixtures

1. Pick a `fixture_id` matching the pattern: `art_<NNN>` / `arx_<NNN>` / `yt_<NNN>`. Increment within the existing range; ids are stable handles that cache keys depend on.
2. Append the row to `extraction_eval.jsonl` (one row per line, no trailing comma).
3. Validate: `uv run python -c "from pathlib import Path; from evals.core import load_fixtures; h,r=load_fixtures(Path('packages/evals/datasets/extraction/extraction_eval.jsonl'), expected_schema_version=1); print(len(r))"`
4. Score the new set via a workbench notebook (`run_variants`) or `run_benchmark` to confirm it loads + scores.

### Cadence review

Refresh when:
- `eval-corpus` tooling lands (Step 6) → swap the whole set for raw_store-derived fixtures with real v5 baseline expected output.
- A new `content_type` ships in production extraction (e.g., podcast, book) → add 5 of the new type before promoting the prompts for it.
- Score variance between v5 and v6 candidate runs is dominated by 1–2 fixtures → add diversity in that content_type to dilute the outlier.
- Quarterly (whichever sooner), revisit topic diversity — the user's interests drift over time.

## `narrative_coverage_gold.jsonl`

> Written to the five-heading dataset contract described above. The other sections predate it and
> are being migrated (issue #236).
>
> **Two documents cover this dataset and the split is deliberate.** This section records what was
> produced and how to read a number off it. [`narrative_coverage_codebook.md`](narrative_coverage_codebook.md)
> holds the labelling instructions and *only* those — it is handed verbatim to a labeler, so it
> carries no prior labels, no scores and no record of earlier rounds, any of which would anchor
> the next labeler. Facts about outcomes belong here; instructions belong there.

### What it decides

**Which narrative extraction prompt covers the source better.** One arm against another, on the
same run.

It is a **report, not a gate**: the verdict is a mean of ≥3 full re-runs with its observed range,
never a single number, and nothing in CI blocks on it.

- Metric: `coverage@present` — of the gold threads for a fixture, the fraction the candidate
  `narrative_md` surfaces. Denominator is the fixture's `gold_threads`, so it moves whenever the
  dataset does.
- Scored by `evals.extraction.NarrativeCoverageScorer`, run by `uv run eval-narrative-coverage`.
- `content_shape` is carried for **stratified reporting only** — the scorer does not route on it.

**What it does not decide.** It says nothing about whether the narrative is *true* (no fabrication
axis — that is `narrative_fidelity_gold_seed.jsonl`), nothing about the delivery-layer sections
`narrative_v3` adds, and nothing about absolute quality: it measures coverage of *these* threads,
which are one drafted reading of each source.

### Provenance

Two labelling generations, and they were not produced the same way.

- **The original seven** were drafted by a chunked cross-family read — long sources split
  ~35K with 2K overlap, each chunk read for micro-points, then consolidated to
  **distinct-follow-up grain** (one thread per point a listener could ask a *separate* follow-up
  about). Grain is load-bearing: at coarse 7-bucket grain every narrative scores ~100% and the
  divergence only appears at follow-up grain.
- **The three added later** were drafted by a blind cross-family jury — one Gemini-backed and one
  Claude-backed labeler per fixture, given the source and
  [`narrative_coverage_codebook.md`](narrative_coverage_codebook.md) only, with no system output
  and no knowledge of the prompt under test. A same-family labeler was excluded deliberately: the
  extraction model under test is an OpenAI model, and a same-vendor judge self-prefers.

  **Two rounds were run and neither converged.** Round 1 used a count-and-split grain rule; round
  2 replaced it with the functional "a point plus its supporting reason is one thread" test the
  codebook now carries. Round 2 did **not** improve agreement — on the shortest fixture the two
  labelers moved from 24-vs-23 threads to 8-vs-27, and divergence across all four labelings
  reached 3.4×. A third round was judged unlikely to help.

  They are therefore merged by **union across all four labelings**, mechanically deduped with
  nothing dropped, following the FN-heavy loss: a gold that under-lists threads cannot score a
  genuine omission *as* an omission, so it flatters every system it measures. Union is a merge
  policy, not a consensus claim — read the counts as an upper-bound reading of each source.

**Blind-scored, partial = absent (FN-heavy loss).** A thread counts as covered only if the
narrative surfaces its **specific anchor** — a name, number, mechanism, or concrete example. A
vague or figure-dropping mention is absent. The asymmetry is deliberate: a coverage number that
rewards near-misses hides exactly the regressions this eval exists to catch.

### How to read a number off it

1. **Not comparable across dataset revisions.** Union merging lifted thread counts, so any score
   published against an earlier revision is not a reference for one taken now. Compare two prompts
   by running both arms in a single pass (`--narrative … --baseline …`) so they share a model and
   a judge.
2. **Not comparable across fixtures.** Drafted density spans one thread per 51 characters to one
   per 13,173 — a 259× spread, though 45× among the Latin-script fixtures alone; a character
   count is not a density guide across scripts. Either way a fixture's absolute rate says more
   about how finely it was labelled than about the prompt.
3. **The judge is the measurement floor.** Present/absent is an LLM verdict, one batched call per
   fixture. Its dangerous direction is the **false positive** — calling a thread present when the
   anchor is missing — which inflates coverage and masks regressions. Treat the automated absolute
   as a few points lenient; the **delta** is the trustworthy signal, not the level.
4. **Never headline a single run.** A single run can land as an unlucky draw — one matched-format
   run put a delta at +1.3pt where the 3-run mean put it at +12.6pt. Record the mean of ≥3 full
   re-runs (extraction + judge) with the range. Per-shape and per-type cells are n=1 per fixture
   and swing more; read them as directional.

### Identity and safe changes

- The header carries `gold_version`, bumped on any change to the scoring population or the labels
  — **not** only on schema changes. `eval-narrative-coverage` reads it into the run manifest and
  the printed Notion row (`Dataset=narrative_coverage_gold.jsonl@vN`) so a logged score can be
  traced to the data that produced it.
- **Adding a fixture changes the denominator**, so it is a revision bump and it invalidates
  comparison against prior scores. Prefer adding to close a *named* coverage gap, and record which
  gap in the commit.
- **Existing fixtures are immutable.** The original seven are carried byte-identical across
  revisions; re-labelling one silently rewrites history for every score already logged against it.
- Validate with `uv run eval-narrative-coverage --dry-run`, which loads the gold, reports the
  fixture and thread counts it actually parsed, and prices the run before spending anything.

### Refresh policy

Refresh when a new `content_type` ships in production extraction, when the corpus shape drifts, or
when the narrative prompt changes enough that the gold's anchors no longer represent live content.
Re-run on any model bump as a regression floor.

**Top-up** (add fixtures, bump the revision) when a coverage gap is named and the existing fixtures
still represent live content. **Rebuild** when the labelling method itself changes — mixing
generations is what produced the density spread in point 2 above.
