"""Extract-claims benchmark — score the producer over the pinned cohort on
faithfulness (grounded_fraction), tagging accuracy, and claim volume, aggregated
per content shape. `run_source` wires producer + judges (injected for tests);
`main` runs the real judges over the cohort and prints the report.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .dataset import DATASET_PATH, load_source_fixtures
from .faithfulness import fixture_to_item


@dataclass(frozen=True)
class SourceResult:
    id: str
    content_shape: str
    n_claims: int
    n_opinion: int
    grounded_fraction: float
    tagging_accuracy: float


@dataclass(frozen=True)
class ShapeAgg:
    content_shape: str
    n_sources: int
    mean_grounded: float
    mean_tagging: float
    mean_claims: float
    mean_opinion_rate: float


def run_source(
    fx, *, extract_claims_fn: Callable, faithfulness_judge: Any, tagging_judge: Any
) -> SourceResult:
    """Extract claims from one fixture, then score faithfulness + tagging over its claims."""
    claim_set, _ = extract_claims_fn(fixture_to_item(fx), content_shape=fx.content_shape)
    page = "\n".join(f"- {c.text}" for c in claim_set.claims)
    fs = faithfulness_judge.score(page=page, sources=[fx.body])
    ts = tagging_judge.score(claims=claim_set.claims, source=fx.body)
    return SourceResult(
        id=fx.id,
        content_shape=fx.content_shape,
        n_claims=len(claim_set.claims),
        n_opinion=sum(c.speculative for c in claim_set.claims),
        grounded_fraction=fs.grounded_fraction,
        tagging_accuracy=ts.accuracy,
    )


def aggregate(results: list[SourceResult]) -> list[ShapeAgg]:
    by_shape: dict[str, list[SourceResult]] = {}
    for r in results:
        by_shape.setdefault(r.content_shape, []).append(r)
    out = []
    for shape, rs in sorted(by_shape.items()):
        n = len(rs)
        claims = sum(r.n_claims for r in rs)
        opinion = sum(r.n_opinion for r in rs)
        out.append(
            ShapeAgg(
                content_shape=shape,
                n_sources=n,
                mean_grounded=sum(r.grounded_fraction for r in rs) / n,
                mean_tagging=sum(r.tagging_accuracy for r in rs) / n,
                mean_claims=claims / n,
                mean_opinion_rate=opinion / claims if claims else 0.0,
            )
        )
    return out


def format_report(aggs: list[ShapeAgg]) -> str:
    header = f"{'shape':18} {'n':>2} {'faithful':>9} {'tagging':>8} {'claims/src':>11} {'opin%':>6}"
    lines = [header, "-" * len(header)]
    for a in aggs:
        lines.append(
            f"{a.content_shape:18} {a.n_sources:>2} {a.mean_grounded:>9.2%} "
            f"{a.mean_tagging:>8.2%} {a.mean_claims:>11.1f} {a.mean_opinion_rate:>6.0%}"
        )
    return "\n".join(lines)


def main() -> None:
    """`eval-extract-claims [--limit N]` — run the real judges over the cohort."""
    from workflows.wiki_synthesis.extract_claims import EXTRACT_CLAIMS_MODEL, extract_claims

    from evals.core.manifest import RunManifest, code_rev, format_manifest_line
    from evals.wiki.chat import JUDGE_MODEL, make_faithfulness_chat_fn, make_tagging_chat_fn
    from evals.wiki.judges import FaithfulnessJudge, TaggingJudge

    from .dataset import SCHEMA_VERSION

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="score only the first N sources")
    args = ap.parse_args()

    manifest = RunManifest(
        dataset=DATASET_PATH.name,
        dataset_schema=SCHEMA_VERSION,
        subject="extract_claims",  # the producer under test
        subject_model=EXTRACT_CLAIMS_MODEL,
        judge_model=JUDGE_MODEL,
        code_rev=code_rev(),
        mode="report",
        runs=1,
    )
    print(format_manifest_line(manifest))

    fixtures = load_source_fixtures(DATASET_PATH)
    if args.limit:
        fixtures = fixtures[: args.limit]
    faith = FaithfulnessJudge(chat_fn=make_faithfulness_chat_fn())
    tag = TaggingJudge(chat_fn=make_tagging_chat_fn())

    results: list[SourceResult] = []
    errored: list[str] = []
    for fx in fixtures:
        try:
            r = run_source(
                fx, extract_claims_fn=extract_claims, faithfulness_judge=faith, tagging_judge=tag
            )
        except Exception as e:  # a flaky judge call shouldn't sink the whole run
            errored.append(fx.id)
            print(f"  {fx.content_shape:18} {fx.id:18} ERROR: {e}")
            continue
        results.append(r)
        print(
            f"  {fx.content_shape:18} {fx.id:18} claims={r.n_claims:>3} "
            f"grounded={r.grounded_fraction:.0%} tagging={r.tagging_accuracy:.0%}"
        )
    print("\n" + format_report(aggregate(results)))
    if errored:
        print(f"\n{len(errored)} errored: {errored}")


if __name__ == "__main__":
    main()
