"""Faithfulness scorer over the source-summary cohort — wiring + aggregation
TDD'd with fakes; the real judge/producer run in the benchmark (S5)."""

from dataclasses import dataclass

from domains.wiki.source_summary import SourceClaim, SourceSummary
from evals.wiki.source_summary.dataset import SourceFixture
from evals.wiki.source_summary.faithfulness import (
    SourceFaithfulness,
    aggregate_by_shape,
    score_faithfulness,
)


def _fx(fid="art_1", shape="tutorial") -> SourceFixture:
    return SourceFixture(
        id=fid, content_shape=shape, title="T", content_date=None, body="the source body"
    )


@dataclass
class _FakeClaim:
    text: str
    supported: bool


@dataclass
class _FakeScore:
    claims: list
    grounded_fraction: float


class _FakeJudge:
    """Marks the 2nd claim unsupported → grounded 0.5."""

    def score(self, *, page, sources):
        lines = [ln[2:] for ln in page.splitlines() if ln.startswith("- ")]
        claims = [_FakeClaim(text=t, supported=(i == 0)) for i, t in enumerate(lines)]
        grounded = sum(c.supported for c in claims) / len(claims) if claims else 1.0
        return _FakeScore(claims=claims, grounded_fraction=grounded)


def _fake_summarize(item, *, content_shape=None):
    summary = SourceSummary(
        item_id=item.item_id,
        content_date=None,
        claims=[
            SourceClaim(text="grounded claim", source_id=item.item_id),
            SourceClaim(text="fabricated claim", source_id=item.item_id, speculative=True),
        ],
    )
    return summary, None


def test_score_faithfulness_runs_producer_and_judge():
    result = score_faithfulness(_fx(), summarize_fn=_fake_summarize, judge=_FakeJudge())
    assert result.id == "art_1"
    assert result.n_claims == 2
    assert result.grounded_fraction == 0.5
    assert result.unsupported == ["fabricated claim"]


def test_aggregate_by_shape_means_per_shape():
    results = [
        SourceFaithfulness("a", "tutorial", 4, 1.0, []),
        SourceFaithfulness("b", "tutorial", 6, 0.5, ["x"]),
        SourceFaithfulness("c", "podcast_episode", 10, 0.8, []),
    ]
    by_shape = {s.content_shape: s for s in aggregate_by_shape(results)}
    assert by_shape["tutorial"].mean_grounded == 0.75
    assert by_shape["tutorial"].n_sources == 2
    assert by_shape["tutorial"].total_claims == 10
    assert by_shape["podcast_episode"].mean_grounded == 0.8
