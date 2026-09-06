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
# # `<mode>_<knob>__<scope>` — TEMPLATE
#
# What this notebook does. One sentence.
#
# **Inputs:** which fixtures, which content_id, which variants.
# **Outputs:** what `RESULTS` contains; what the render cell shows.

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "_template"
FIXTURE_SET = "packages/evals/datasets/extraction_eval.jsonl"
CONTENT_ID_INDEX = 0
MAX_FIXTURES = 5
MAX_COST_USD_PER_RUN = 0.50

RESULTS: dict = {}

# %% tags=["imports"]
import os
from pathlib import Path

from evals.core import CostBudget, load_fixtures
from evals.extraction import (
    ExtractionFixture,
    make_three_call_variant,
    run_variants,
)


def _repo_root() -> Path:
    # Kernel cwd is packages/evals/notebooks/ when launched via `poe jupyter`;
    # walk up until we find pyproject.toml so paths resolve from anywhere.
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml + packages/ in any parent)")


SERVICE_URL = os.environ.get("FETCHER_URL", "http://localhost:8000")

REPO_ROOT = _repo_root()

# %% tags=["load"]
header, rows = load_fixtures(REPO_ROOT / FIXTURE_SET, expected_schema_version=1)
fixtures = [
    ExtractionFixture(
        fixture_id=r["fixture_id"],
        content_type=r["content_type"],
        content=r["content"],
        expected_topic_card=r["expected_topic_card"],
    )
    for r in rows[:MAX_FIXTURES]
]
print(f"loaded {len(fixtures)} fixtures from {FIXTURE_SET}")

# %% tags=["adapter"]
# Wire variants here. Name a prompt by its label — the basename of a file under
# prompts/extraction/ — and the service resolves it. To try a candidate, write
# it as a new file there and name it, so the run measures a prompt that exists
# rather than a string that lived only in this notebook.
variants = [
    make_three_call_variant(
        name="v5_baseline",
        prompt_versions={"narrative": "narrative_v3", "topic_card": "topic_card_v1", "followups": "followups_v1"},
        model="gpt-4o-mini",
        service_url=SERVICE_URL,
    ),
]

# %% tags=["fire"]
budget = CostBudget(max_cost_usd_per_run=MAX_COST_USD_PER_RUN)
records = run_variants(variants, fixtures, budget=budget, fixture_set=FIXTURE_SET)
RESULTS["records"] = records
print(f"ran {len(records)} variants × {len(fixtures)} fixtures")

# %% tags=["render"]
# Eyeball. Per-notebook render shape lives here — table for topic_card,
# markdown diff for narrative, list diff for followups.
for rec in RESULTS["records"]:
    print(f"\n=== {rec.variant_name} ({rec.run_id}) ===")
    for sample in rec.samples:
        card = (sample.output or {}).get("topic_card", {})
        print(f"  {sample.fixture_id}: {card.get('extracted_title', '<no title>')}")

# %% tags=["score"]
# Optional in workbench — scoring is benchmark's job. Wire TopicCardScorer in
# here only if a single-fixture score helps the iteration.
RESULTS["scores"] = None

# %% tags=["act"]
# What did this run tell you? Promote a variant to prompts/extraction/?
# Open a follow-up PR? Park the finding in ai-findings/?
# Write the next move so the notebook stays a decision artefact.
