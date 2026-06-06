"""End-to-end notebook smoke — papermill runs ab_topic_card__content with mocked LLM.

The topic_card notebook is the canonical smoke target; narrative + followups
notebooks are renderer variations on the same harness, so they don't need
separate end-to-end coverage. If this passes, the harness wiring is sound.
"""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _kernel_available() -> bool:
    """kp-eval kernel must be registered; otherwise papermill can't run.

    Registered via: uv run --extra notebooks python -m ipykernel install
        --user --name kp-eval --display-name "kp-eval (Python 3.13)"
    """
    try:
        from jupyter_client.kernelspec import KernelSpecManager
    except ImportError:
        return False
    try:
        KernelSpecManager().get_kernel_spec("kp-eval")
        return True
    except Exception:
        return False


@pytest.fixture
def fake_three_call_extract(monkeypatch):
    """Replace ThreeCallOpenAIExtractor.extract with a deterministic stub."""
    fake_topic_card = MagicMock()
    fake_topic_card.model_dump.return_value = {
        "extracted_title": "Stub title",
        "core_mechanism": "Stub mechanism",
        "best_example": "Stub example",
        "main_tension": "Stub tension",
        "transferable_pattern": "Stub pattern",
        "candidate_tie_backs": ["Stub tie"],
    }
    fake_followups = MagicMock()
    fake_followups.model_dump.return_value = {"questions": ["q1", "q2"]}

    fake_payload = MagicMock()
    fake_payload.narrative_md = "Stub narrative markdown."
    fake_payload.topic_card = fake_topic_card
    fake_payload.followups = fake_followups

    fake_record = MagicMock(tokens_in=100, tokens_out=200, duration_ms=1.0)

    def fake_extract(self, content, *, content_type):
        return fake_payload, [fake_record, fake_record, fake_record]

    from workflows.extraction import ThreeCallOpenAIExtractor

    monkeypatch.setattr(ThreeCallOpenAIExtractor, "extract", fake_extract)


_KERNEL_SKIP_REASON = (
    "kp-eval kernel not registered; run one-time: "
    "`uv run --extra notebooks python -m ipykernel install --user --name kp-eval`"
)


@pytest.mark.skipif(not _kernel_available(), reason=_KERNEL_SKIP_REASON)
def test_ab_topic_card_notebook_runs_end_to_end(tmp_path, fake_three_call_extract):
    """papermill executes the notebook with mocked LLM — passes if no exceptions.

    Patching ThreeCallOpenAIExtractor in the parent process doesn't reach the
    kernel subprocess papermill spawns. Instead we rewrite the notebook's
    adapter cell to use stub Variants — exercises every other cell (config,
    load, fire, render, score, act) and confirms the public API of
    evals.extraction is wired correctly.
    """
    papermill = pytest.importorskip("papermill")
    nb_in = Path("packages/evals/notebooks/ab_topic_card__content.ipynb")
    nb_out = tmp_path / "out.ipynb"
    eval_runs = tmp_path / "eval_runs"

    os.environ["OPENAI_API_KEY"] = "stub-not-used"

    # Rewrite the adapter cell to a no-op variant before running.
    src = json.loads(nb_in.read_text())
    for cell in src["cells"]:
        tags = cell.get("metadata", {}).get("tags", [])
        if "adapter" in tags:
            cell["source"] = [
                "from evals.core import (\n",
                "    FixtureRun, RunStatus, Variant, VariantProvenance,\n",
                ")\n",
                "\n",
                "def _stub_run(fixture):\n",
                "    return FixtureRun(\n",
                "        fixture_id=fixture.fixture_id,\n",
                "        status=RunStatus.SUCCESS,\n",
                "        output={\n",
                "            'narrative_md': 'stub',\n",
                "            'topic_card': {'extracted_title': fixture.fixture_id},\n",
                "            'followups': {'questions': []},\n",
                "        },\n",
                "        stages=[], tokens_in=100, tokens_out=200,\n",
                "        cost_usd=0.0, duration_ms=1,\n",
                "    )\n",
                "\n",
                "def _mk(name):\n",
                "    return Variant(\n",
                "        name=name, config={}, run=_stub_run,\n",
                "        provenance=VariantProvenance(\n",
                "            prompt_versions={}, model_versions={}, code_revision='x',\n",
                "            corpus_anchor=None, output_schema_version=1,\n",
                "        ),\n",
                "    )\n",
                "\n",
                "variants = [_mk('baseline'), _mk('candidate')]\n",
            ]
    patched_in = tmp_path / "patched_in.ipynb"
    patched_in.write_text(json.dumps(src))

    # Run from repo root so prompts/extraction/ + datasets paths resolve.
    repo_root = Path.cwd()
    papermill.execute_notebook(
        str(patched_in),
        str(nb_out),
        parameters={
            "CONTENT_ID_INDEX": 0,
            "MAX_COST_USD_PER_RUN": 1.0,
            "FIXTURE_SET": str(repo_root / "packages/evals/datasets/extraction_eval.jsonl"),
        },
        kernel_name="kp-eval",
        cwd=str(repo_root),
    )

    nb = json.loads(nb_out.read_text())
    fire_cell = next(c for c in nb["cells"] if "fire" in c.get("metadata", {}).get("tags", []))
    outputs = fire_cell.get("outputs", [])
    assert outputs, "fire cell produced no output — variant runs likely failed"

    # Cleanup: the notebook's run_variants() persists to data/eval_runs by default
    # because FIXTURE_SET isn't routed through data_root in the notebook. Sweep
    # any test-generated dirs to keep the working tree clean.
    if eval_runs.exists():
        shutil.rmtree(eval_runs, ignore_errors=True)
