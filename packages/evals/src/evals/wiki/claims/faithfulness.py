"""Faithfulness scoring for extracted claims — is each claim actually in the
source? Reuses `FaithfulnessJudge` from `evals.wiki.judges`: `page` = the
producer's claims, `sources` = the source body. Per-source `grounded_fraction`,
aggregated per content shape.

The producer (`extract_claims`) and the judge are injected so the wiring is
unit-tested with fakes; the real run lives in the benchmark.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from domains.types import IngestItem

from .dataset import SourceFixture


@dataclass(frozen=True)
class SourceFaithfulness:
    id: str
    content_shape: str
    n_claims: int
    grounded_fraction: float
    unsupported: list[str]


@dataclass(frozen=True)
class ShapeFaithfulness:
    content_shape: str
    n_sources: int
    mean_grounded: float
    total_claims: int


def fixture_to_item(fx: SourceFixture) -> IngestItem:
    """The IngestItem the producer extracts claims from — mirrors the asset's queue-row
    build, but sourced from a pinned fixture."""
    return IngestItem(
        item_id=fx.id,
        title=fx.title,
        date=date.fromisoformat(fx.content_date) if fx.content_date else None,
        text=fx.body,
        source_type="eval",
        source_ref=fx.id,
        author=None,
    )


def score_faithfulness(
    fx: SourceFixture, *, extract_claims_fn: Callable, judge: Any
) -> SourceFaithfulness:
    """Extract claims from one source, then judge each claim against the source body."""
    claim_set, _ = extract_claims_fn(fixture_to_item(fx), content_type=fx.content_type)
    page = "\n".join(f"- {c.text}" for c in claim_set.claims)
    fscore = judge.score(page=page, sources=[fx.body])
    return SourceFaithfulness(
        id=fx.id,
        content_shape=fx.content_shape,
        n_claims=len(claim_set.claims),
        grounded_fraction=fscore.grounded_fraction,
        unsupported=[c.text for c in fscore.claims if not c.supported],
    )


def aggregate_by_shape(results: list[SourceFaithfulness]) -> list[ShapeFaithfulness]:
    """Per-shape mean grounded-fraction (unweighted over sources)."""
    by_shape: dict[str, list[SourceFaithfulness]] = {}
    for r in results:
        by_shape.setdefault(r.content_shape, []).append(r)
    return [
        ShapeFaithfulness(
            content_shape=shape,
            n_sources=len(rs),
            mean_grounded=sum(r.grounded_fraction for r in rs) / len(rs),
            total_claims=sum(r.n_claims for r in rs),
        )
        for shape, rs in sorted(by_shape.items())
    ]
