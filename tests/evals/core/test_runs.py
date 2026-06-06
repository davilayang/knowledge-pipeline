"""RunRecord persistence to data/eval_runs/{kind}/{target}/{version}/{run_id}/run.json."""

import json

from evals.core.runs import load_run, run_dir, save_run
from evals.core.types import RunRecord, VariantProvenance


def _provenance() -> VariantProvenance:
    return VariantProvenance(
        prompt_versions={"extraction.youtube": "v5_2026_06_01"},
        model_versions={"extraction": "gpt-4o-mini"},
        code_revision="abc1234",
        corpus_anchor=None,
        output_schema_version=1,
    )


def _record(run_id: str = "01HXY") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        kind="workbench",
        target="extraction",
        variant_name="v5_baseline",
        variant_config={"prompt_label": "v5_kp_copy_2026_06_01"},
        variant_provenance=_provenance(),
        fixture_set="packages/evals/datasets/extraction_eval.jsonl",
        fixture_anchor="2026-05-30",
        started_at="2026-06-06T10:00:00Z",
        completed_at="2026-06-06T10:00:42Z",
        samples=[],
        scores=[],
        config={},
    )


def test_run_dir_layout(tmp_path):
    d = run_dir(
        root=tmp_path,
        kind="workbench",
        target="extraction",
        version="v1",
        run_id="01HXY",
    )
    assert d == tmp_path / "workbench" / "extraction" / "v1" / "01HXY"


def test_save_creates_directory_and_writes_run_json(tmp_path):
    rec = _record()
    path = save_run(root=tmp_path, version="v1", record=rec)
    assert path.exists()
    assert path.name == "run.json"
    data = json.loads(path.read_text())
    assert data["run_id"] == "01HXY"
    assert data["target"] == "extraction"
    assert data["kind"] == "workbench"


def test_load_returns_run_record(tmp_path):
    rec = _record()
    save_run(root=tmp_path, version="v1", record=rec)
    loaded = load_run(
        root=tmp_path,
        kind="workbench",
        target="extraction",
        version="v1",
        run_id="01HXY",
    )
    assert loaded.run_id == rec.run_id
    assert loaded.variant_name == rec.variant_name
    assert loaded.variant_provenance.code_revision == "abc1234"


def test_layout_mirrors_inspect_eval_shape(tmp_path):
    """Persistence layout: top-level run_id/target/kind/version/config + nested
    results.scores + samples — close enough to Inspect AI's .eval shape that a
    future adapter is trivial."""
    rec = _record()
    save_run(root=tmp_path, version="v1", record=rec)
    data = json.loads(
        (tmp_path / "workbench" / "extraction" / "v1" / "01HXY" / "run.json").read_text()
    )
    assert {"run_id", "target", "kind", "config", "samples", "results"} <= set(data)
    assert "scores" in data["results"]
