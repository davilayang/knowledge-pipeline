# `knowledge-evals` datasets

Pinned eval inputs for the per-pipeline harnesses (`evals.retrieval`, `evals.extraction`, future `evals.workflows`). Lives alongside the package source — each dataset is part of its harness's contract, not an unrelated top-level artefact.

**Header convention is not uniform.** The extraction-family datasets open with a header line
carrying `schema_version`, which `evals.core.load_fixtures` validates and rejects on mismatch.
The retrieval datasets carry no header at all, and `narrative_fidelity_gold_seed.jsonl` carries
`schema_version` on **every row** rather than in a header. Check the dataset's own section before
assuming a loader applies.

## What a dataset section records

Each section should document a **contract**, not the file's contents. Anything derivable from the
JSONL — counts, fixture tables, per-fixture stats — belongs out of the prose: it is duplication
that goes stale, and it is what has gone stale here before. Five headings:

**Two sections follow this so far** — `narrative_coverage_gold.jsonl` and `extract_metadata_gold.jsonl`.
The other six predate it and still carry derivable counts; migrating them is tracked in issue #236.

| heading | records |
|---|---|
| **What it decides** | the question this set answers, and explicitly the ones it does not; whether it is a gate or a report; the metric, its denominator, and the entry-point command |
| **Provenance** | who or what produced the labels, blind or not, cross-family or not, and what disagreement was observed — the trust basis for every number it yields |
| **How to read a number off it** | what silently invalidates a comparison |
| **Identity and safe changes** | what bumps the revision, what is immutable, and the command that validates a change |
| **Refresh policy** | cadence, and what triggers a rebuild rather than a top-up |

**Scores do not belong here.** Headline results go to the Notion Eval Runs database and detailed
run JSON stays local and gitignored — see [`../README.md`](../README.md). A score written into
this file is a number nothing will ever update.

## README section vs. sibling codebook

A dataset whose labels are drafted by judgement — rather than derived mechanically — also gets a
codebook, e.g. [`narrative_coverage_codebook.md`](extraction/narrative_coverage_codebook.md). The split is by
**audience**, not by level of detail:

- **The codebook is handed to a labeler, verbatim, as their only instruction.** It therefore holds
  instructions and nothing else: no prior labels, no scores, no record of how earlier rounds went.
  Any of that anchors the next labeler and costs the blindness the gold depends on.
- **The README section is never handed to a labeler**, so outcomes live here.

The test for where a new fact goes: *would handing this to a labeler bias them?* → README.
*Does a labeler need it to do the job?* → codebook. A dataset with mechanically-derived rows needs
no codebook; adding one only creates a second place to go stale.

## Datasets in subfolders

A dataset family in its own folder keeps its README there. The five headings above apply to each section; sections that predate them are tracked in issue #236.

- [`search/`](search/README.md): recall probes, graded gold and the snapshot manifest for the search service.
- [`retrieval/`](retrieval/README.md): `retrieval_eval.jsonl` and the two `wiki_eval_*.jsonl` slices, read by `evals.retrieval`.
- [`extraction/`](extraction/README.md): `extraction_eval.jsonl`, `narrative_coverage_gold.jsonl` and `extract_metadata_gold.jsonl` with their codebooks, and `narrative_fidelity_gold_seed.jsonl`, read by `evals.extraction`, the extraction notebooks and the extract_metadata prompt's scoring.
- [`wiki/`](wiki/README.md): `extract_claims_eval.jsonl` and `extract_claims_tagging_gold.jsonl`, read by `evals.wiki.claims`.

