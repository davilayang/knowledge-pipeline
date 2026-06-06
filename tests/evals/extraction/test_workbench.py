"""Tests for evals.extraction.workbench — single-fixture and multi-variant helpers."""

import json

import pytest
from evals.core import (
    BudgetExceededError,
    CostBudget,
    FixtureRun,
    RunStatus,
    Variant,
    VariantProvenance,
)
from evals.extraction.types import ExtractionFixture
from evals.extraction.workbench import run_variant, run_variants


def _stub_variant(name: str, output: dict, cost: float = 0.01) -> Variant:
    def _run(fixture):
        return FixtureRun(
            fixture_id=fixture.fixture_id,
            status=RunStatus.SUCCESS,
            output=output,
            stages=[],
            tokens_in=100,
            tokens_out=200,
            cost_usd=cost,
            duration_ms=42,
        )

    return Variant(
        name=name,
        config={"stub": True},
        provenance=VariantProvenance(
            prompt_versions={},
            model_versions={},
            code_revision="x",
            corpus_anchor=None,
            output_schema_version=1,
        ),
        run=_run,
    )


def _fixture(fid: str = "f1") -> ExtractionFixture:
    return ExtractionFixture(
        fixture_id=fid,
        content_type="Article",
        content="C",
        expected_topic_card={},
    )


def test_run_variant_returns_fixture_run():
    v = _stub_variant("v1", {"topic_card": {"extracted_title": "T"}})
    fr = run_variant(v, _fixture())
    assert fr.status == RunStatus.SUCCESS
    assert fr.output["topic_card"]["extracted_title"] == "T"


def test_run_variants_returns_one_record_per_variant(tmp_path):
    fixtures = [_fixture()]
    variants = [
        _stub_variant("a", {"topic_card": {}}),
        _stub_variant("b", {"topic_card": {}}),
    ]
    records = run_variants(
        variants,
        fixtures,
        budget=CostBudget(max_cost_usd_per_run=1.0),
        data_root=tmp_path,
    )
    assert len(records) == 2
    assert {r.variant_name for r in records} == {"a", "b"}
    assert all(r.kind == "workbench" for r in records)
    assert all(r.target == "extraction" for r in records)


def test_run_variants_persists_run_json(tmp_path):
    variants = [_stub_variant("a", {"topic_card": {}})]
    records = run_variants(
        variants,
        [_fixture()],
        budget=CostBudget(max_cost_usd_per_run=1.0),
        data_root=tmp_path,
    )
    rec = records[0]
    expected_path = tmp_path / "workbench" / "extraction" / "v1" / rec.run_id / "run.json"
    assert expected_path.exists()
    payload = json.loads(expected_path.read_text())
    assert payload["variant_name"] == "a"


def test_run_variants_skips_persist_when_false(tmp_path):
    variants = [_stub_variant("a", {"topic_card": {}})]
    records = run_variants(
        variants,
        [_fixture()],
        budget=CostBudget(max_cost_usd_per_run=1.0),
        data_root=tmp_path,
        persist=False,
    )
    assert records  # returned in-memory
    assert not any(tmp_path.rglob("run.json"))


def test_run_variants_aborts_when_estimate_exceeds_budget(tmp_path):
    """Budget check is launch-time when `estimated_usd` is provided."""
    variants = [_stub_variant("a", {"topic_card": {}})]
    with pytest.raises(BudgetExceededError):
        run_variants(
            variants,
            [_fixture()],
            budget=CostBudget(max_cost_usd_per_run=0.50),
            data_root=tmp_path,
            estimated_usd=1.00,
        )
