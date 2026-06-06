"""DiffReport + text + HTML renderers."""

from evals.core.diff import DiffReport, diff_runs, render_diff_html, render_diff_text
from evals.core.types import FixtureRun, RunRecord, RunStatus, VariantProvenance


def _provenance() -> VariantProvenance:
    return VariantProvenance(
        prompt_versions={},
        model_versions={},
        code_revision="x",
        corpus_anchor=None,
        output_schema_version=1,
    )


def _record(name: str, output_a: dict, output_b: dict) -> RunRecord:
    return RunRecord(
        run_id=name,
        kind="workbench",
        target="extraction",
        variant_name=name,
        variant_config={},
        variant_provenance=_provenance(),
        fixture_set="x",
        fixture_anchor=None,
        started_at="t0",
        completed_at="t1",
        samples=[
            FixtureRun(
                fixture_id="f1",
                status=RunStatus.SUCCESS,
                output=output_a,
                stages=[],
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                duration_ms=0,
            ),
            FixtureRun(
                fixture_id="f2",
                status=RunStatus.SUCCESS,
                output=output_b,
                stages=[],
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                duration_ms=0,
            ),
        ],
        scores=[],
        config={},
    )


def test_diff_runs_returns_per_fixture_per_field_diff():
    a = _record("A", {"title": "X", "mechanism": "Y"}, {"title": "P"})
    b = _record("B", {"title": "X", "mechanism": "Y'"}, {"title": "Q"})
    report = diff_runs(a, b)
    assert isinstance(report, DiffReport)
    # f1.title same; f1.mechanism differs; f2.title differs
    f1 = report.per_fixture["f1"]
    assert f1["title"] == ("X", "X")
    assert f1["mechanism"] == ("Y", "Y'")
    assert report.per_fixture["f2"]["title"] == ("P", "Q")


def test_render_diff_text_contains_variant_names():
    a = _record("v5_a", {"title": "X"}, {"title": "P"})
    b = _record("v5_b", {"title": "X"}, {"title": "Q"})
    out = render_diff_text(diff_runs(a, b))
    assert "v5_a" in out and "v5_b" in out


def test_render_diff_html_returns_html_string():
    a = _record("v5_a", {"title": "X"}, {"title": "P"})
    b = _record("v5_b", {"title": "X"}, {"title": "Q"})
    out = render_diff_html(diff_runs(a, b))
    assert out.lstrip().startswith("<")
    assert "v5_a" in out and "v5_b" in out


def test_field_picker_filters_keys():
    a = _record("A", {"title": "X", "mechanism": "Y", "noise": 1}, {"title": "P"})
    b = _record("B", {"title": "X", "mechanism": "Y'", "noise": 2}, {"title": "Q"})
    report = diff_runs(a, b, field_picker=lambda k: k in {"title", "mechanism"})
    assert "noise" not in report.per_fixture["f1"]
