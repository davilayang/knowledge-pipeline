# `services/fetcher/evals/`

Fidelity evals for the fetcher's cloud-LLM stages. Dev-only: the Docker image
copies `src/`, `config/`, and `prompts/`, so nothing here ships in the service.

## Why these live here and not in `packages/evals`

`services/fetcher` is not a uv workspace member (own `pyproject.toml`/lock/venv),
so `knowledge-evals` cannot import it. A/B'ing two prompts also needs passing one
*into* the chain call, which `POST /v1/structure` doesn't expose — it only ever
runs the prompt the service booted with. Living here lets the harness import the
production `fetcher.extractors._cloud_chain.call_cloud_chain` directly, so there
is one copy of the call path instead of a drift-prone reimplementation.

Tradeoff: these evals don't share `evals.core`'s substrate (typed Variants,
`RunManifest` provenance, snapshot/diff) — they should adopt it once the
`eval-*` CLIs get a shared run-layer. No console script here in the meantime,
to avoid adding a fourth entrypoint ahead of that consolidation.

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
fixture is a **regression guard** instead — nothing here changes the transcript
structurer. It shows a single call *can* hold fidelity above 100,000 characters,
not that one reliably does: the same endpoint has been observed collapsing an
input to under 20% of its length, and the trigger is unidentified. `--fetches-db`
is only needed for that fixture.

Report the mean of at least 3 runs with its observed range, never a single run.
The headline goes to the Knowledge OS — Eval Runs Notion database; detailed
output stays local.

### What the score does not measure

Four limits, all load-bearing when reading a result:

- **Recall only, no precision.** It never penalises added text or kept
  boilerplate — a prompt can raise its score by retaining more chrome, not more
  article, and this metric can't tell the two apart.
- **Correctly-deleted boilerplate counts as loss**, since the denominator is the
  entire raw input — every source has its own floor set by how much chrome it
  carries. Only meaningful against the **same fixture under a different
  prompt**, never across fixtures.
- **Position-blind.** Membership is checked against the whole output, so text
  that survived but moved scores clean — no positional breakdown. Bucketing by
  source line doesn't work here: line-sparse input like a caption blob can be a
  single 100k-char line that swallows a whole bucket. If shape analysis is
  needed later, bucket by character position instead.
- **Blind to short lines and punctuation.** Lines under three normalised tokens
  contribute nothing, and normalisation strips Markdown syntax, operators, and
  indentation — so it cannot verify that fenced code, tables, or config came
  through verbatim. That check stays manual.

**Article fixtures score the cloud stage in isolation**, not the endpoint: they
skip the trafilatura first stage of the cascade and do not pass the title /
author / date hints that `POST /v1/structure` forwards in production.

**Transcript fixtures go through `structure_transcript` itself**, because
chunking and that lane's tighter retention floor live in that function. Calling
the chain directly would score a path production never takes, and would
silently skip the behaviour these fixtures exist to check.
