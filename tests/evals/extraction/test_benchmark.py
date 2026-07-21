"""Tests for evals.extraction.benchmark — scored aggregation + CLI."""

from evals.core import FieldScore, FixtureRun, RunStatus, Variant, VariantProvenance
from evals.extraction.benchmark import run_benchmark
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
            metadata={"judge_per_field": {"extracted_title": "exact"}},
        )


def test_run_benchmark_emits_scores_per_field(tmp_path):
    rec = run_benchmark(
        variant=_variant("v5"),
        fixtures=_fixtures(),
        scorer=_PerfectScorer(),
        data_root=tmp_path,
        persist=False,
    )
    assert rec.kind == "benchmark"
    assert rec.target == "extraction"
    assert rec.config == {}  # no manifest supplied → config stays empty
    assert len(rec.scores) == 1
    assert rec.scores[0].metrics["extracted_title"] == 1.0
    assert rec.scores[0].metrics["__overall__"] == 1.0


def test_run_benchmark_stratifies_by_content_type(tmp_path):
    fixtures = [
        ExtractionFixture(
            fixture_id="a",
            content_type="Article",
            content="c",
            expected_topic_card={"extracted_title": "T"},
        ),
        ExtractionFixture(
            fixture_id="b",
            content_type="YouTube",
            content="c",
            expected_topic_card={"extracted_title": "T"},
        ),
    ]
    rec = run_benchmark(
        variant=_variant("v5"),
        fixtures=fixtures,
        scorer=_PerfectScorer(),
        data_root=tmp_path,
        persist=False,
    )
    strats = rec.scores[0].stratifications
    assert "by_content_type" in strats
    assert strats["by_content_type"]["Article"] == 1.0
    assert strats["by_content_type"]["YouTube"] == 1.0


def test_run_benchmark_stratifies_by_content_shape_when_present(tmp_path):
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
    rec = run_benchmark(
        variant=_variant("v5"),
        fixtures=fixtures,
        scorer=_PerfectScorer(),
        data_root=tmp_path,
        persist=False,
    )
    by_shape = rec.scores[0].stratifications["by_content_shape"]
    assert by_shape == {"prose": 1.0, "dense": 1.0}


def test_run_benchmark_skips_scoring_for_errored_runs(tmp_path):
    def _failing_run(f):
        return FixtureRun(
            fixture_id=f.fixture_id,
            status=RunStatus.ERROR,
            output=None,
            stages=[],
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            duration_ms=5,
            error_message="upstream 500",
        )

    failing = Variant(
        name="failing",
        config={},
        run=_failing_run,
        provenance=VariantProvenance(
            prompt_versions={},
            model_versions={},
            code_revision="x",
            corpus_anchor=None,
            output_schema_version=1,
        ),
    )
    rec = run_benchmark(
        variant=failing,
        fixtures=_fixtures(2),
        scorer=_PerfectScorer(),
        data_root=tmp_path,
        persist=False,
    )
    assert rec.scores[0].sample_count == 2
    assert rec.scores[0].metrics == {}
