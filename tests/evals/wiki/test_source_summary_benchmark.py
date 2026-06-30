"""Source-summary benchmark — per-source run wiring + per-shape aggregation/report.
Pure parts TDD'd; the real judges run in the CLI."""

from domains.wiki.source_summary import SourceClaim, SourceSummary
from evals.wiki.source_summary.benchmark import (
    SourceResult,
    aggregate,
    format_report,
    run_source,
)
from evals.wiki.source_summary.dataset import SourceFixture


def _fx(fid="art_1", shape="tutorial"):
    return SourceFixture(id=fid, content_shape=shape, title="T", content_date=None, body="body")


class _FaithJudge:
    def score(self, *, page, sources):
        from evals.wiki.judges import Claim, FaithfulnessScore

        lines = [ln[2:] for ln in page.splitlines() if ln.startswith("- ")]
        claims = [Claim(text=t, supported=(i == 0)) for i, t in enumerate(lines)]
        g = sum(c.supported for c in claims) / len(claims) if claims else 1.0
        return FaithfulnessScore(claims=claims, unsupported_count=0, grounded_fraction=g)


class _TagJudge:
    def score(self, *, claims, source):
        from evals.wiki.judges import TaggingScore

        return TaggingScore(verdicts=[], accuracy=1.0)


def _summarize(item, *, content_shape=None):
    s = SourceSummary(
        item_id=item.item_id,
        content_date=None,
        claims=[
            SourceClaim(text="c1", source_id=item.item_id),
            SourceClaim(text="c2", source_id=item.item_id, speculative=True),
        ],
    )
    return s, None


def test_run_source_combines_faithfulness_and_tagging():
    r = run_source(
        _fx(), summarize_fn=_summarize, faithfulness_judge=_FaithJudge(), tagging_judge=_TagJudge()
    )
    assert r.id == "art_1"
    assert r.n_claims == 2
    assert r.n_speculation == 1
    assert r.grounded_fraction == 0.5
    assert r.tagging_accuracy == 1.0


def test_aggregate_and_report_per_shape():
    results = [
        SourceResult("a", "tutorial", 4, 0, 1.0, 1.0),
        SourceResult("b", "tutorial", 6, 1, 0.8, 0.9),
        SourceResult("c", "podcast_episode", 20, 12, 0.9, 0.7),
    ]
    agg = {a.content_shape: a for a in aggregate(results)}
    assert agg["tutorial"].mean_grounded == 0.9
    assert agg["tutorial"].mean_tagging == 0.95
    assert agg["tutorial"].mean_claims == 5.0
    report = format_report(aggregate(results))
    assert "tutorial" in report and "podcast_episode" in report
