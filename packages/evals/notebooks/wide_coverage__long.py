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
# # `wide_coverage__long` — cite-by-index coverage on long content
#
# Run the cite-by-index **wide** extractor over one long fixture
# (`extraction_eval_long.jsonl`): the source is numbered `[i]`, each claim cites
# the unit indices supporting it. `coverage()` then reports, deterministically,
# how much of the document (which deciles, incl. the tail) the *faithful* claims
# ground.
#
# **Use this when:** deciding whether the wide schema reaches the document tail
# well enough to justify building the A6 chunking pipeline.
#
# **Don't use this for:** a fixed-vs-wide head-to-head (the fixed TopicCard can't
# cite indices — that comparison was dropped as unfair). This measures the wide
# arm's absolute coverage + faithfulness.

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "wide_coverage__long"
FIXTURE_SET = "packages/evals/datasets/extraction_eval_long.jsonl"
CONTENT_ID_INDEX = 0  # 0=article 53K, 1=youtube 80K, 2=arxiv 253K
FIXED_TOPIC_CARD = "topic_card_v1.md"  # prompt body reused, cardinality overridden
MODEL = "gpt-4.1-mini"  # prod extraction model

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
    make_wide_variant,
    openai_wide_extract_fn,
)


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
print(f"using fixture {fixture.fixture_id} ({fixture.content_type}, {len(fixture.content):,} chars, {len(units)} units)")

# %% tags=["adapter"]
PROMPTS = REPO_ROOT / "prompts" / "extraction"
wide_prompt_text = (PROMPTS / FIXED_TOPIC_CARD).read_text() + WIDE_ITEMS_INSTRUCTION
api_key = os.environ["OPENAI_API_KEY"]

wide_variant = make_wide_variant(
    name="wide",
    prompt_text=wide_prompt_text,
    model=MODEL,
    extract_fn=openai_wide_extract_fn(api_key=api_key, model=MODEL, prompt_text=wide_prompt_text),
)

# %% tags=["fire"]
run = wide_variant.run(fixture)
RESULTS["run"] = run
print(f"  status={run.status}, tokens={run.tokens_in}+{run.tokens_out}, claims={len(run.output['claims']) if run.output else 0}")

# %% tags=["score"]
from evals.extraction.wide import Claim

claims = [Claim(**c) for c in (run.output or {}).get("claims", [])]
cov = coverage(units, claims)
RESULTS["coverage"] = cov

# %% tags=["render"]
from IPython.display import HTML, display

# Decile histogram of supported claims (faithful only).
from evals.extraction.verify import verify_grounding

grounded, _ = verify_grounding(claims, units)
hist = [0] * 10
for c in grounded:
    for i in c.cited_indices:
        hist[min(9, i * 10 // len(units))] += 1

metrics = ["supported_claims", "unsupported_claims", "distinct_span_coverage", "tail_coverage", "redundancy"]
rows_html = [f"<tr><td><b>{m}</b></td><td style='text-align:right'>{cov[m]}</td></tr>" for m in metrics]
display(HTML("<table><tr><th>metric</th><th>wide</th></tr>" + "".join(rows_html) + "</table>"))
print("supported-claim citations per decile (early→late):")
print("  " + " ".join(f"{h:>3}" for h in hist))

# %% tags=["act"]
# distinct_span_coverage high AND tail_coverage non-trivial across CONTENT_ID_INDEX
# 0,1,2? That's the evidence gate to graduate the A6 chunking build. A low
# tail_coverage that DOESN'T improve with wide schema means single-pass can't
# reach the tail no matter the schema → chunking (A6) is the actual lever.
