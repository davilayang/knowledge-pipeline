# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.3
#   kernelspec:
#     display_name: kp-eval (Python 3.13)
#     language: python
#     name: kp-eval
# ---

# %% [markdown] tags=["header"]
# # `layered_coverage__long` — does chunking recover the document tail?
#
# Runs two arms over one long fixture (`extraction_eval_long.jsonl`) and compares
# their cite-by-index `coverage()`:
#
# - **wide-single** — one wide extraction pass over the whole numbered source
#   (the baseline; grounds ~nothing past decile 6 on long docs).
# - **layered** — chunk the *globally* numbered source, extract per chunk,
#   concatenate claims. Because indices stay global, the merged claims feed the
#   same `coverage()` unchanged.
#
# **The gate:** layered must lift `tail_coverage` (deciles 7–9) meaningfully over
# wide-single at a tolerable token multiple. If it doesn't, chunking isn't the
# lever after all. If it does, layered extraction is worth wiring into the
# pipeline as an offline shadow extractor (run alongside prod, not replacing it).
#
# **Not measured here:** duplicate/false-merge rate (merge is plain concat — the
# `redundancy` metric flags whether semantic dedup is worth building next).

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "layered_coverage__long"
FIXTURE_SET = "packages/evals/datasets/extraction_eval_long.jsonl"
CONTENT_ID_INDEX = 0  # 0=article 53K, 1=youtube 80K, 2=arxiv 253K
FIXED_TOPIC_CARD = "topic_card_v1.md"  # prompt body reused, cardinality overridden
MODEL = "gpt-4.1-mini"  # prod extraction model
BUDGET_CHARS = 8000  # ~2K tokens/chunk — uniform budget so each region gets equal slots
OVERLAP_UNITS = 2  # re-include a couple of sentences per boundary

RESULTS: dict = {}

# %% tags=["imports"]
import os
from pathlib import Path

from evals.core import load_fixtures
from evals.extraction import (
    WIDE_ITEMS_INSTRUCTION,
    ExtractionFixture,
    citable_units,
    coverage,
    make_layered_extract_fn,
    make_wide_variant,
    openai_chunk_extract_fn,
    openai_wide_extract_fn,
)
from evals.extraction.wide import Claim


def _repo_root() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml + packages/ in any parent)")


REPO_ROOT = _repo_root()

# %% tags=["load"]
header, rows = load_fixtures(REPO_ROOT / FIXTURE_SET, expected_schema_version=1)
row = rows[CONTENT_ID_INDEX]
fixture = ExtractionFixture(
    fixture_id=row["fixture_id"],
    content_type=row["content_type"],
    content=row["content"],
    expected_topic_card=row["expected_topic_card"],
)
units = citable_units(fixture.content)
print(
    f"using fixture {fixture.fixture_id} ({fixture.content_type}, "
    f"{len(fixture.content):,} chars, {len(units)} units)"
)

# %% tags=["adapter"]
PROMPTS = REPO_ROOT / "prompts" / "extraction"
wide_prompt_text = (PROMPTS / FIXED_TOPIC_CARD).read_text() + WIDE_ITEMS_INSTRUCTION
api_key = os.environ["OPENAI_API_KEY"]

wide_variant = make_wide_variant(
    name="wide-single",
    prompt_text=wide_prompt_text,
    model=MODEL,
    extract_fn=openai_wide_extract_fn(api_key=api_key, model=MODEL, prompt_text=wide_prompt_text),
)
layered_variant = make_wide_variant(
    name="layered",
    prompt_text=wide_prompt_text,
    model=MODEL,
    extract_fn=make_layered_extract_fn(
        chunk_extract_fn=openai_chunk_extract_fn(
            api_key=api_key, model=MODEL, prompt_text=wide_prompt_text
        ),
        budget_chars=BUDGET_CHARS,
        overlap_units=OVERLAP_UNITS,
    ),
)

# %% tags=["fire"]
runs = {v.name: v.run(fixture) for v in (wide_variant, layered_variant)}
RESULTS["runs"] = runs
for name, run in runs.items():
    n_claims = len(run.output["claims"]) if run.output else 0
    print(
        f"  {name:>12}: status={run.status}, tokens={run.tokens_in}+{run.tokens_out}, claims={n_claims}"
    )

# %% tags=["score"]
covs = {}
for name, run in runs.items():
    claims = [Claim(**c) for c in (run.output or {}).get("claims", [])]
    covs[name] = coverage(units, claims)
RESULTS["coverage"] = covs

# %% tags=["render"]
from IPython.display import HTML, display

from evals.extraction.verify import verify_grounding

metrics = [
    "supported_claims",
    "unsupported_claims",
    "distinct_span_coverage",
    "tail_coverage",
    "redundancy",
]
head = "<tr><th>metric</th>" + "".join(f"<th>{n}</th>" for n in covs) + "</tr>"
body = "".join(
    "<tr><td><b>"
    + m
    + "</b></td>"
    + "".join(f"<td style='text-align:right'>{covs[n][m]}</td>" for n in covs)
    + "</tr>"
    for m in metrics
)
display(HTML("<table>" + head + body + "</table>"))

print("supported-claim citations per decile (early→late):")
for name, run in runs.items():
    grounded, _ = verify_grounding(
        [Claim(**c) for c in (run.output or {}).get("claims", [])], units
    )
    hist = [0] * 10
    for c in grounded:
        for i in c.cited_indices:
            hist[min(9, i * 10 // len(units))] += 1
    print(f"  {name:>12}: " + " ".join(f"{h:>3}" for h in hist))

# %% tags=["act"]
# GATE: layered tail_coverage >= wide-single across CONTENT_ID_INDEX 0,1,2 at a
# tolerable token multiple → chunking is the tail lever, worth wiring in as an
# offline shadow extractor. If layered doesn't lift the tail deciles, chunking
# isn't it. tail_coverage is the fraction of tail deciles (7,8,9) grounded, so it
# is volume-independent — a more-verbose arm can't win or lose it on claim count.
tc_wide = covs["wide-single"]["tail_coverage"]
tc_layered = covs["layered"]["tail_coverage"]
print(
    f"\ntail_coverage: wide-single={tc_wide:.3f}  layered={tc_layered:.3f}  delta={tc_layered - tc_wide:+.3f}"
)
