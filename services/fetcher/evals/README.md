# `services/fetcher/evals/`

Fidelity evals for the fetcher's cloud-LLM stages. Dev-only: the Docker image
copies `src/`, `config/`, and `prompts/`, so nothing here ships in the service.

## Why these live here and not in `packages/evals`

`services/fetcher` is not a uv workspace member — it has its own `pyproject.toml`,
lock file, and venv — so `knowledge-evals` cannot import it. And A/B'ing two
prompts needs one passed *into* the chain call, which `POST /v1/structure` does
not expose; the endpoint only ever runs the prompt the service booted with.
Living here lets the harness import the production
`fetcher.extractors._cloud_chain.call_cloud_chain`, so there is a single copy of
the call path rather than a re-implementation that can drift.

The cost is that these evals do not share `evals.core`'s substrate (typed
Variants, `RunManifest` provenance, snapshot/diff). They should adopt the shared
runner when the run-layer consolidation across the `eval-*` CLIs lands. Until
then there is deliberately **no console script** here — a fourth entrypoint
would add to the problem that consolidation exists to fix.

## What is here

| File | Role |
|---|---|
| `structure_fidelity.py` | Scorer + A/B runner for `POST /v1/structure`'s cloud stage. Run with `python evals/structure_fidelity.py`. |
| `datasets/` | Pinned fixture manifests. See that directory's README for the pinning contract. |

## `structure_fidelity.py`

Measures whether the structurer strips boilerplate **without rewriting the
article** — its one hard requirement, and the one it fails silently by
summarising instead.

Metric is **trigram recall**: the share of the raw input's three-word sequences
still present in the output. Paraphrasing destroys trigrams, so a rewrite scores
low even when the prose reads well and every heading survives.

```bash
set -a && source .env && set +a && \
  uv run python evals/structure_fidelity.py \
    --queue-db /path/to/queue.db --fetches-db /path/to/fetches.db \
    --prompt prompts/structure_v2.md --baseline prompts/structure_v1.md --runs 3
```

Article fixtures run as an A/B between the two prompts. The one transcript
fixture runs as a **regression guard** against a floor instead: nothing here
changes the transcript structurer. It shows a single call *can* hold fidelity at
over 100,000 characters — not that one reliably does. The same endpoint has been
observed collapsing a ~90k transcript to 15-18% of its length, where chunking
the identical input to ~12k windows recovered 98%, so the behaviour is bimodal
and the trigger is unidentified. `--fetches-db` is only needed for that fixture.

Report the mean of at least 3 runs with its observed range, never a single run.
The headline goes to the Knowledge OS — Eval Runs Notion database; detailed
output stays local.

### What the score does not measure

Four limits, all load-bearing when reading a result:

- **Recall only, no precision.** It never penalises text the output *added* or
  boilerplate it *kept*. A prompt can raise its score by retaining more chrome
  rather than by preserving more article, and this metric cannot tell those apart.
- **Correctly-deleted boilerplate counts as loss**, because the denominator is
  the entire raw input. Every source has its own floor set by how much chrome it
  carries. A score is only meaningful against the **same fixture under a
  different prompt** — never against a different fixture.
- **Position-blind.** Membership is checked against the whole output, so text
  that survived but moved scores clean. There is deliberately no positional
  breakdown: the earlier one bucketed by source line, which is meaningless on
  line-sparse input like a caption blob, where a single 100k-char line swallows
  a whole bucket. If a future regression needs shape analysis, bucket by
  character position.
- **Blind to short lines and to punctuation.** Lines under three normalised
  tokens contribute nothing, and normalisation strips Markdown syntax, operators,
  and indentation — so this cannot verify that fenced code, tables, or config
  snippets came through verbatim. That check is still by hand.

**Article fixtures score the cloud stage in isolation**, not the endpoint: they
skip the trafilatura first stage of the cascade and do not pass the title /
author / date hints that `POST /v1/structure` forwards in production.

**Transcript fixtures go through `structure_transcript` itself**, because
chunking and that lane's tighter retention floor live in that function. Calling
the chain directly would score a path production never takes — and would
silently fail to exercise the behaviour the transcript fixtures exist to check.
