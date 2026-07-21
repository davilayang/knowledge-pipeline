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
# # `ab_topic_card__content` — Topic-card prompt A/B on one content
#
# Iterate the `topic_card_v1.md` prompt against one fixture from
# `extraction_eval.jsonl`. Side-by-side renders both variants' six Topic
# Card fields as an HTML table — the diff is eyeballable per-field.
#
# **Use this when:** you've drafted a candidate `topic_card_v2.md` and want
# a single-content read before a corpus-wide benchmark.
#
# **Don't use this for:** corpus-wide scoring (use `run_benchmark` / `run_variants`).

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "ab_topic_card__content"
FIXTURE_SET = "packages/evals/datasets/extraction_eval.jsonl"
CONTENT_ID_INDEX = 0
BASELINE_TOPIC_CARD = "topic_card_v1.md"
CANDIDATE_TOPIC_CARD = "topic_card_v1.md"  # override to candidate when iterating
MAX_COST_USD_PER_RUN = 0.25
MODEL = "gpt-4o-mini"

RESULTS: dict = {}

# %% tags=["imports"]
import os
from pathlib import Path

from domains.extraction.prompts import strip_design_notes
from evals.core import CostBudget, load_fixtures
from evals.extraction import (
    ExtractionFixture,
    TopicCardFields,
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
narrative_text = strip_design_notes((PROMPTS / "narrative_v1.md").read_text())
followups_text = strip_design_notes((PROMPTS / "followups_v1.md").read_text())
baseline_text = strip_design_notes((PROMPTS / BASELINE_TOPIC_CARD).read_text())
candidate_text = strip_design_notes((PROMPTS / CANDIDATE_TOPIC_CARD).read_text())

variants = [
    make_three_call_variant(
        name="baseline",
        narrative_prompt_text=narrative_text,
        topic_card_prompt_text=baseline_text,
        followups_prompt_text=followups_text,
        prompt_versions={
            "narrative": "v1",
            "topic_card": BASELINE_TOPIC_CARD.replace(".md", ""),
            "followups": "v1",
        },
        model=MODEL,
        api_key=os.environ["OPENAI_API_KEY"],
    ),
    make_three_call_variant(
        name="candidate",
        narrative_prompt_text=narrative_text,
        topic_card_prompt_text=candidate_text,
        followups_prompt_text=followups_text,
        prompt_versions={
            "narrative": "v1",
            "topic_card": CANDIDATE_TOPIC_CARD.replace(".md", ""),
            "followups": "v1",
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
# Per-field HTML table — six rows, baseline vs candidate.
from IPython.display import HTML, display

records = RESULTS["records"]
baseline_card = (records[0].samples[0].output or {}).get("topic_card", {})
candidate_card = (records[1].samples[0].output or {}).get("topic_card", {})

rows_html = []
for field in TopicCardFields.canonical():
    a = baseline_card.get(field, "<missing>")
    b = candidate_card.get(field, "<missing>")
    if isinstance(a, list):
        a = "<br>".join(str(x) for x in a)
    if isinstance(b, list):
        b = "<br>".join(str(x) for x in b)
    rows_html.append(
        f"<tr><td><b>{field}</b></td>"
        f"<td style='vertical-align:top;width:45%'>{a}</td>"
        f"<td style='vertical-align:top;width:45%'>{b}</td></tr>"
    )

display(
    HTML(
        "<table style='border-collapse:collapse'>"
        "<tr><th></th><th>baseline</th><th>candidate</th></tr>" + "".join(rows_html) + "</table>"
    )
)

# %% tags=["score"]
RESULTS["scores"] = None

# %% tags=["act"]
# If candidate wins on this content, re-run with CONTENT_ID_INDEX=1, 2, … on
# 2-3 more fixtures. Then promote: copy candidate_text into
# prompts/extraction/<new>.md, update EXTRACT_QUEUE_PROMPT_LABEL_<CT> in .env,
# run `run_variants` / `run_benchmark` over the fixture set for the scored corpus run.
