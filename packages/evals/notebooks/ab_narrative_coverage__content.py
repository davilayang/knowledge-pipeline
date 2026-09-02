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
# # `ab_narrative_coverage__content` — scored narrative coverage on one gold item
#
# Runs the current prod narrative prompt against one `narrative_coverage_gold`
# fixture and scores it with `NarrativeCoverageScorer` — the per-thread
# present/absent table makes a coverage regression obvious (which specific
# threads the narrative drops). Aggregate numbers live in the CLI/runner; this
# is the eyeball surface for *why* a number moved.

# %% tags=["config", "parameters"]
NOTEBOOK_STEM = "ab_narrative_coverage__content"
FIXTURE_SET = "packages/evals/datasets/narrative_coverage_gold.jsonl"
CONTENT_ID_INDEX = 0
NARRATIVE = "narrative_v3.md"  # the prompt under test
MODEL = "gpt-4.1-mini"
MAX_TOKENS = 4096
MAX_COST_USD_PER_RUN = 0.50

RESULTS: dict = {}

# %% tags=["imports"]
import os
from pathlib import Path

from domains.extraction.prompts import strip_design_notes
from evals.core import CostBudget, load_fixtures
from evals.extraction import (
    ExtractionFixture,
    NarrativeCoverageScorer,
    make_three_call_variant,
    run_variants,
)


def _repo_root() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    raise RuntimeError("Could not locate repo root")


REPO_ROOT = _repo_root()

# %% tags=["load"]
header, rows = load_fixtures(REPO_ROOT / FIXTURE_SET, expected_schema_version=1)
row = rows[CONTENT_ID_INDEX]
fixture = ExtractionFixture(
    fixture_id=row["fixture_id"],
    content_type=row["content_type"],
    content=row["content"],
    expected_topic_card={},
    content_shape=row.get("content_shape"),
    gold_threads=row["gold_threads"],
)
print(f"fixture {fixture.fixture_id} ({fixture.content_shape}) — {len(fixture.gold_threads)} gold threads")

# %% tags=["adapter"]
PROMPTS = REPO_ROOT / "prompts" / "extraction"
variant = make_three_call_variant(
    name="candidate",
    narrative_prompt_text=strip_design_notes((PROMPTS / NARRATIVE).read_text()),
    topic_card_prompt_text=strip_design_notes((PROMPTS / "topic_card_v1.md").read_text()),
    followups_prompt_text=strip_design_notes((PROMPTS / "followups_v1.md").read_text()),
    prompt_versions={"narrative": NARRATIVE.replace(".md", ""), "topic_card": "v1", "followups": "v1"},
    model=MODEL,
    api_key=os.environ["OPENAI_API_KEY"],
    max_tokens=MAX_TOKENS,
)

# %% tags=["fire"]
budget = CostBudget(max_cost_usd_per_run=MAX_COST_USD_PER_RUN)
records = run_variants([variant], [fixture], budget=budget, fixture_set=FIXTURE_SET)
RESULTS["record"] = records[0]
sample = records[0].samples[0]
print(f"  status={sample.status}, tokens={sample.tokens_in}+{sample.tokens_out}, ${sample.cost_usd:.4f}")

# %% tags=["score"]
import json

import openai

client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _judge(prompt: str) -> dict:
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=2048,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(resp.choices[0].message.content or "{}")


scorer = NarrativeCoverageScorer(chat_fn=_judge)
score = scorer.score_run(fixture=fixture, run=records[0].samples[0])
RESULTS["score"] = score
print(f"coverage@present = {score.value['__overall__']:.3f}")

# %% tags=["render"]
# Per-thread hit/miss — the surface that shows WHICH threads the narrative drops.
from IPython.display import HTML, display

per_thread = RESULTS["score"].metadata["per_thread"]
rows_html = "".join(
    f"<tr><td>{'✅' if present else '❌'}</td>"
    f"<td style='padding:4px 8px'>{thread}</td></tr>"
    for thread, present in per_thread.items()
)
display(
    HTML(
        f"<p><b>{fixture.fixture_id}</b> — coverage "
        f"{score.value['__overall__']:.0%} "
        f"({int(sum(per_thread.values()))}/{len(per_thread)})</p>"
        "<table style='border-collapse:collapse'>" + rows_html + "</table>"
    )
)

# %% tags=["act"]
# A miss (❌) is a thread the narrative dropped its anchor for. Read the two
# together — if the narrative genuinely covers a ❌ thread, that's a judge false
# positive/negative to fold into calibration; if it doesn't, it's a real gap for
# the next prompt iteration to close.
