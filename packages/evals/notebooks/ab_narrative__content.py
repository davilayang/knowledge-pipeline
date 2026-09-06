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
# # `ab_narrative__content` — Narrative prompt A/B on one content
#
# Iterate the `narrative_v3.md` prompt against one fixture. Narrative output
# is unstructured markdown — the render is a two-column side-by-side of the
# raw narrative_md output, so prose differences are immediately visible.
#
# **Note on caching:** swapping narrative invalidates OpenAI's prompt prefix
# cache that the three-call extractor relies on for the structured pair.
# Expect the candidate run to cost more tokens than the baseline.

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "ab_narrative__content"
FIXTURE_SET = "packages/evals/datasets/extraction_eval.jsonl"
CONTENT_ID_INDEX = 0
BASELINE_NARRATIVE = "narrative_v3.md"
CANDIDATE_NARRATIVE = "narrative_v3.md"  # override to candidate when iterating
# Both must be prompts written against the current `Narrative`: the extractor
# generates the field list from that model, so an older body would be sent
# with a schema it does not describe. `make_three_call_variant` refuses it.
MAX_COST_USD_PER_RUN = 0.25
MODEL = "gpt-4o-mini"
# The fetcher service that runs the extraction. Point it at a dev instance with
# this repo's prompts/ mounted to score a candidate prompt before it ships.
SERVICE_URL = os.environ.get("FETCHER_URL", "http://localhost:8000")

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
topic_card_text = strip_design_notes((PROMPTS / "topic_card_v1.md").read_text())
followups_text = strip_design_notes((PROMPTS / "followups_v1.md").read_text())
baseline_text = strip_design_notes((PROMPTS / BASELINE_NARRATIVE).read_text())
candidate_text = strip_design_notes((PROMPTS / CANDIDATE_NARRATIVE).read_text())

variants = [
    make_three_call_variant(
        name="baseline",
        prompt_versions={
            "narrative": BASELINE_NARRATIVE.replace(".md", ""),
            "topic_card": "topic_card_v1",
            "followups": "followups_v1",
        },
        model=MODEL,
        service_url=SERVICE_URL,
    ),
    make_three_call_variant(
        name="candidate",
        prompt_versions={
            "narrative": CANDIDATE_NARRATIVE.replace(".md", ""),
            "topic_card": "topic_card_v1",
            "followups": "followups_v1",
        },
        model=MODEL,
        service_url=SERVICE_URL,
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
# Two-column markdown side-by-side of the narrative_md output.
from IPython.display import HTML, display

records = RESULTS["records"]
baseline_narrative = (records[0].samples[0].output or {}).get("narrative_md", "<missing>")
candidate_narrative = (records[1].samples[0].output or {}).get("narrative_md", "<missing>")

display(
    HTML(
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr><th style='width:50%'>baseline</th><th style='width:50%'>candidate</th></tr>"
        f"<tr><td style='vertical-align:top;padding:8px;white-space:pre-wrap'>{baseline_narrative}</td>"
        f"<td style='vertical-align:top;padding:8px;white-space:pre-wrap'>{candidate_narrative}</td></tr>"
        "</table>"
    )
)

# %% tags=["score"]
RESULTS["scores"] = None

# %% tags=["act"]
# Narrative drives downstream structured-call quality via the prompt cache.
# A narrative win is harder to detect than topic_card; sample on ≥5 fixtures
# before promoting. Watch token cost on the candidate — cache misses are real.
