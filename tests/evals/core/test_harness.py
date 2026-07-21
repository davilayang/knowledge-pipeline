"""Tests for evals.core.harness — the shared run-layer.

Parity target: run_and_report reproduces the RunRecord shape run_benchmark
produced before the refactor, and additionally rides the RunManifest in config.
"""

import pytest
from evals.core import FieldScore, FixtureRun, RunStatus, Variant, VariantProvenance
from evals.core.harness import RepeatedReport, run_and_report, run_repeated
from evals.core.manifest import RunManifest
from evals.extraction.types import ExtractionFixture


def _variant(name: str) -> Variant:
    def _run(f):
        return FixtureRun(
            fixture_id=f.fixture_id,
            status=RunStatus.SUCCESS,
            output={"topic_card": {"extracted_title": "T"}},
            stages=[],
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.01,
            duration_ms=10,
        )

    return Variant(
        name=name,
        config={},
        run=_run,
        provenance=VariantProvenance(
            prompt_versions={},
            model_versions={},
            code_revision="x",
            corpus_anchor=None,
            output_schema_version=1,
        ),
    )


def _fixtures(n=3, content_type="Article"):
    return [
        ExtractionFixture(
            fixture_id=f"f{i}",
            content_type=content_type,
            content="c",
            expected_topic_card={"extracted_title": "T"},
        )
        for i in range(n)
    ]


class _PerfectScorer:
    name = "TopicCardScorer"

    def score_run(self, *, fixture, run):
        return FieldScore(
            value={"extracted_title": 1.0, "__overall__": 1.0},
            metadata={},
        )


def test_run_and_report_matches_benchmark_shape_and_attaches_manifest(tmp_path):
    manifest = RunManifest(
        dataset="fx.jsonl",
        dataset_schema=1,
        subject="v5",
        subject_model="gpt-4.1-mini",
        judge_model=None,
        code_rev="abc123",
        mode="report",
        runs=1,
    )
    rec = run_and_report(
        variant=_variant("v5"),
        fixtures=_fixtures(),
        scorer=_PerfectScorer(),
        target="extraction",
        manifest=manifest,
        data_root=tmp_path,
        persist=False,
    )
    # parity with run_benchmark's RunRecord shape
    assert rec.kind == "benchmark"
    assert rec.target == "extraction"
    assert rec.scores[0].metrics["__overall__"] == 1.0
    assert rec.scores[0].stratifications["by_content_type"]["Article"] == 1.0
    # new: the manifest rides in config
    assert rec.config["dataset"] == "fx.jsonl"
    assert rec.config["mode"] == "report"


def test_run_and_report_without_manifest_leaves_config_empty(tmp_path):
    """No manifest supplied → config is left empty (run_benchmark's default path)."""
    rec = run_and_report(
        variant=_variant("v5"),
        fixtures=_fixtures(),
        scorer=_PerfectScorer(),
        target="extraction",
        data_root=tmp_path,
        persist=False,
    )
    assert rec.config == {}


def test_run_and_report_stratifies_by_content_shape(tmp_path):
    fixtures = [
        ExtractionFixture(
            fixture_id="a",
            content_type="youtube",
            content="c",
            expected_topic_card={"extracted_title": "T"},
            content_shape="prose",
        ),
        ExtractionFixture(
            fixture_id="b",
            content_type="arxiv",
            content="c",
            expected_topic_card={"extracted_title": "T"},
            content_shape="dense",
        ),
    ]
    rec = run_and_report(
        variant=_variant("v5"),
        fixtures=fixtures,
        scorer=_PerfectScorer(),
        target="extraction",
        data_root=tmp_path,
        persist=False,
    )
    assert rec.scores[0].stratifications["by_content_shape"] == {"prose": 1.0, "dense": 1.0}


class _VaryingScorer:
    """Scores 0.5 on the first run, 1.0 on the second — to exercise mean + range."""

    name = "VaryingScorer"

    def __init__(self):
        self._call = 0

    def score_run(self, *, fixture, run):
        # bump once per fixture; overall alternates 0.5 (run 1) then 1.0 (run 2)
        val = 0.5 if self._call < 3 else 1.0
        self._call += 1
        return FieldScore(value={"__overall__": val}, metadata={})


def test_run_repeated_carries_mean_and_observed_range(tmp_path):
    manifest = RunManifest(
        dataset="fx.jsonl",
        dataset_schema=1,
        subject="v5",
        subject_model="gpt-4.1-mini",
        judge_model="gpt-4.1-mini",
        code_rev="abc123",
        mode="report",
        runs=2,
    )
    report = run_repeated(
        variant=_variant("v5"),
        fixtures=_fixtures(3),
        scorer=_VaryingScorer(),
        manifest=manifest,
        runs=2,
        target="extraction",
        data_root=tmp_path,
        persist=False,
    )
    assert isinstance(report, RepeatedReport)
    assert len(report.records) == 2
    assert report.per_run == pytest.approx([0.5, 1.0])
    assert report.mean == pytest.approx(0.75)
    assert report.lo == pytest.approx(0.5)
    assert report.hi == pytest.approx(1.0)
    # stratification means averaged across runs
    assert report.by_stratum["by_content_type"]["Article"] == pytest.approx(0.75)
