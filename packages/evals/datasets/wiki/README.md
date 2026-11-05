# Wiki claim datasets

Read by `evals.wiki.claims`. Section conventions: [`../README.md`](../README.md#what-a-dataset-section-records).

## `extract_claims_eval.jsonl`

Pinned cohort for the extract-claims producer eval (`evals.wiki.claims`).
Header `schema_version=2`, `fixture_kind="extract_claims"`; rows are
`{id, content_shape, content_type, title, content_date, body}`. Loaded by
`evals.wiki.claims.dataset.load_source_fixtures`.

`content_type` is required — the loader reads `r["content_type"]`, so a row copied
from the v0 shape below raises `KeyError`. It decides whether a fixture receives the
transcript `[opinion]` prime, so a blank or mis-cased value silently scores an
unprimed cohort. `content_shape` is the per-genre reporting stratification only.

### v1 (2026-08-26) — one tutorial swapped written → spoken

**Still 12 sources, 2 per content shape**; `content_type` added to every row
(5 medium / 5 youtube / 2 arxiv, against a corpus that is ~54% youtube).

`art_13` (a written Medium tutorial) was replaced by `tut_qYNs80FKIVc`, a spoken
IBM Technology tutorial taken verbatim from production. The v0 cohort could not see
a change to the transcript prime's gate at all: every fixture's genre label was
hand-assigned and correct, so all four spoken fixtures were primed identically
before and after. `art_13` was the cheapest thing to lose — 100% tagging accuracy in
both recorded runs, and a duplicate of `art_0` at ~5x the size.

The `tutorial` pair is now one written (unprimed) and one spoken (primed), so
over-tagging on instructional content reads as a **gap between the two fixtures**.
Compare the per-fixture lines, not the `tutorial` stratum mean — that mean now
averages across the treatment boundary and is not comparable to pre-v1 runs.

Not yet established: the new fixture has not been scored, and the `TaggingJudge`'s
60-claim gold (`extract_claims_tagging_gold.jsonl`) samples six sources whose
tutorial representative is *written*, so a tagging drop on the spoken tutorial is
ambiguous between producer and judge until the disagreements are read by hand.

### v0 (2026-06-30) — bootstrap

**12 sources, 2 per content shape** (tutorial / opinion_essay / conference_talk /
podcast_episode / research_summary / unknown). Bodies are real fetched content:
4 Medium articles, 2 conference-talk transcripts (YouTube auto-subs), 2 podcast
transcripts, 2 arXiv abstracts. The set is deliberately small and shape-balanced
so per-shape faithfulness / tagging / stability are comparable run-over-run;
spoken shapes (talk/podcast) are where tag behaviour varies most.

Assembled by gitignored `data/shadow_audit/build_eval_cohort.py` (provenance);
the JSONL is the checked-in artifact.

## `extract_claims_tagging_gold.jsonl`

Human-labelled gold for calibrating the **tagging** axis — does a claim's tag
match the source? Each row is `{source_id, claim_text, producer_tag, gold_tag}`;
`source_id` joins to a `extract_claims_eval.jsonl` body. Loaded by
`evals.wiki.claims.calibration.load_gold`.

### The taxonomy — `reported` / `opinion`

Each claim carries one of two tags. **These are about the source's *stance*, not
truth:**

- **`reported`** — the source presents it as an established, reported, or
  measured happening: an event, a release, a number, an attributed quote, a
  stated property. A `reported` claim is **not** asserted to be *true* — only
  that the source states it as fact-shaped. (Truth-checking is a separate,
  deferred verifier's job.)
- **`opinion`** — the source's stance: a value-judgment, recommendation,
  interpretation, forecast, or marketing claim. Reported predictions and
  editorialising fused with judgment are `opinion`, even when phrased as fact.

The producer emits these as `[reported]` / `[opinion]` markers on each claim
line; the parser, judge, and gold all use the same two strings. **Naming note:**
these tags were renamed from `fact` / `speculation` — "fact" wrongly implied
*true* when it only ever meant *reported-as-established*. `reported`/`opinion`
names the source's stance, which is what the attributed lane actually routes on.

### v1 (2026-06-30) — human gold

**60 claims, 10 per content shape** (the 6 shapes above), labelled claim-by-claim
by the user against the source. Replaces a v0 17-claim Claude-labelled candidate
(model-vs-model agreement, not ground truth — n=17, Wilson CI ~53–90%).

Calibration against this gold (`data/shadow_audit/run_calibration_v1.py`, gitignored):

| | vs human gold | per-shape weak spot |
|---|---|---|
| **Producer** (gpt-4.1-mini) | 85% (51/60), exact | `unknown` 50% — over-tags unverified news as `reported` |
| **Judge** (gpt-4.1) | ~90% (88–98% across runs) | `unknown` 60–70% — over-calls `opinion` on reported news |

The **producer number is the load-bearing one and is exact** — producer tags are
frozen in the gold, so 51/60 is deterministic. Its only systematic error is
one-directional: tagging unverified news / editorialising as `reported` on
`unknown`-shape content. The **judge number is soft**: it swings ~88–98%
run-to-run (LLM variance — a single run isn't precise to ±5pt and needs N-run
averaging), and the judge sees the producer's tag while grading, so it's an
anchored signal, not fully independent. Provenance: gitignored
`data/shadow_audit/build_gold_review.py` (candidate) → user review.
