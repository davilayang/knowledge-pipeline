"""Smoke + frozen-ness + JSON-serializability for evals.core.types."""

import dataclasses
import json

from evals.core.types import (
    FieldScore,
    FixtureRef,
    FixtureRun,
    RunRecord,
    RunStatus,
    ScoreReport,
    StageTrace,
    VariantProvenance,
)


def _provenance() -> VariantProvenance:
    return VariantProvenance(
        prompt_versions={"extraction.youtube": "v6_2026_06_03"},
        model_versions={"extraction": "gpt-4o-2024-11-20"},
        code_revision="abc1234",
        corpus_anchor="backup_readings/raw_store/2026-05-30",
        output_schema_version=1,
    )


def test_run_status_values():
    assert RunStatus.SUCCESS == "success"
    assert RunStatus.ERROR == "error"
    assert RunStatus.TIMEOUT == "timeout"
    assert RunStatus.SKIPPED == "skipped"


def test_all_types_are_frozen():
    for cls in (
        VariantProvenance,
        FixtureRun,
        RunRecord,
        ScoreReport,
        FixtureRef,
        FieldScore,
        StageTrace,
    ):
        params = cls.__dataclass_params__
        assert params.frozen, f"{cls.__name__} must be frozen"


def test_fixture_run_success_path():
    run = FixtureRun(
        fixture_id="yt_001",
        status=RunStatus.SUCCESS,
        output={"extracted_title": "X"},
        stages=[],
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        duration_ms=1234,
    )
    assert run.error_message is None
    # JSON-serializable
    json.dumps(dataclasses.asdict(run))


def test_fixture_run_error_path():
    run = FixtureRun(
        fixture_id="yt_002",
        status=RunStatus.ERROR,
        output=None,
        stages=[],
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        duration_ms=12,
        error_message="boom",
    )
    assert run.output is None
    assert run.error_message == "boom"


def test_field_score_value_is_per_field_dict():
    """value is a Mapping[str, float] so a single scorer emits per-field scores natively."""
    fs = FieldScore(
        value={"extracted_title": 1.0, "core_mechanism": 0.97},
        metadata={"content_type": "YouTube", "judge_name": "exact"},
    )
    assert fs.value["extracted_title"] == 1.0


def test_run_record_roundtrips_to_json():
    rec = RunRecord(
        run_id="01HXXXXX",
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
        config={"budget": {"max_concurrent_calls": 4}},
    )
    payload = dataclasses.asdict(rec)
    assert json.loads(json.dumps(payload))["run_id"] == "01HXXXXX"


def test_stage_trace_holds_snapshot_dicts():
    trace = StageTrace(
        node="extract_entities",
        input_snapshot={"item_id": "src_1"},
        output_snapshot={"entities": ["e1"]},
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
    )
    json.dumps(dataclasses.asdict(trace))
