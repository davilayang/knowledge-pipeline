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
# # `ab_followups__content` — Followups prompt A/B on one content
#
# Iterate the `followups_v1.md` prompt against one fixture. Followups output
# is a list of question strings — the render is a numbered side-by-side with
# overlap highlighting so you can see which questions both variants agree on
# vs which are unique to one.

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "ab_followups__content"
FIXTURE_SET = "packages/evals/datasets/extraction_eval.jsonl"
CONTENT_ID_INDEX = 0
BASELINE_FOLLOWUPS = "followups_v1.md"
CANDIDATE_FOLLOWUPS = "followups_v1.md"  # override to candidate when iterating
MAX_COST_USD_PER_RUN = 0.25
MODEL = "gpt-4o-mini"

RESULTS: dict = {}

# %% tags=["imports"]
import os
from pathlib import Path

from domains.extraction.prompts import strip_design_notes
from evals.core import CostBudget, load_fixtures
from evals.extraction import ExtractionFixture, make_three_call_variant, run_variants


def _repo_root() -> Path:
    # Kernel cwd is packages/evals/notebooks/ when launched via `poe jupyter`;
    # walk up until we find pyproject.toml so paths resolve from anywhere.
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
print(f"using fixture {fixture.fixture_id} ({fixture.content_type})")

# %% tags=["adapter"]
PROMPTS = REPO_ROOT / "prompts" / "extraction"
narrative_text = strip_design_notes((PROMPTS / "narrative_v3.md").read_text())
topic_card_text = strip_design_notes((PROMPTS / "topic_card_v1.md").read_text())
baseline_text = strip_design_notes((PROMPTS / BASELINE_FOLLOWUPS).read_text())
candidate_text = strip_design_notes((PROMPTS / CANDIDATE_FOLLOWUPS).read_text())

variants = [
    make_three_call_variant(
        name="baseline",
        narrative_prompt_text=narrative_text,
        topic_card_prompt_text=topic_card_text,
        followups_prompt_text=baseline_text,
        prompt_versions={
            "narrative": "v1",
            "topic_card": "v1",
            "followups": BASELINE_FOLLOWUPS.replace(".md", ""),
        },
        model=MODEL,
        api_key=os.environ["OPENAI_API_KEY"],
    ),
    make_three_call_variant(
        name="candidate",
        narrative_prompt_text=narrative_text,
        topic_card_prompt_text=topic_card_text,
        followups_prompt_text=candidate_text,
        prompt_versions={
            "narrative": "v1",
            "topic_card": "v1",
            "followups": CANDIDATE_FOLLOWUPS.replace(".md", ""),
        },
        model=MODEL,
        api_key=os.environ["OPENAI_API_KEY"],
    ),
]

# %% tags=["fire"]
budget = CostBudget(max_cost_usd_per_run=MAX_COST_USD_PER_RUN)
records = run_variants(variants, [fixture], budget=budget, fixture_set=FIXTURE_SET)
RESULTS["records"] = records
for r in records:
    s = r.samples[0]
    print(
        f"  {r.variant_name}: status={s.status}, "
        f"tokens={s.tokens_in}+{s.tokens_out}, ${s.cost_usd:.4f}"
    )

# %% tags=["render"]
# Numbered side-by-side; questions appearing in both variants get a
# background tint so overlap is visible at a glance.
from IPython.display import HTML, display

records = RESULTS["records"]
baseline_fu = (records[0].samples[0].output or {}).get("followups", {})
candidate_fu = (records[1].samples[0].output or {}).get("followups", {})
baseline_qs = baseline_fu.get("questions", []) if isinstance(baseline_fu, dict) else []
candidate_qs = candidate_fu.get("questions", []) if isinstance(candidate_fu, dict) else []

shared = set(baseline_qs) & set(candidate_qs)


def _row(i: int, q: str) -> str:
    bg = "#f0f8ff" if q in shared else ""
    return f"<tr><td style='vertical-align:top;background:{bg}'>{i + 1}. {q}</td></tr>"


display(
    HTML(
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr><th style='width:50%'>baseline</th><th style='width:50%'>candidate</th></tr>"
        "<tr>"
        "<td style='vertical-align:top;padding:8px'><table>"
        + "".join(_row(i, q) for i, q in enumerate(baseline_qs))
        + "</table></td>"
        "<td style='vertical-align:top;padding:8px'><table>"
        + "".join(_row(i, q) for i, q in enumerate(candidate_qs))
        + "</table></td>"
        "</tr></table>"
    )
)
print(
    f"shared: {len(shared)} / unique-to-baseline: {len(set(baseline_qs) - shared)} / unique-to-candidate: {len(set(candidate_qs) - shared)}"
)

# %% tags=["score"]
RESULTS["scores"] = None

# %% tags=["act"]
# Followups feed retrieval queries downstream (NA's recall expansion). A
# candidate that produces more diverse questions for unfamiliar content but
# converges on baseline for familiar content is the shape we want — sample
# on multiple content_types before promoting.
