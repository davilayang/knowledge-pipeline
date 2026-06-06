"""Scored aggregation + CLI for the extraction harness.

`run_benchmark` is the programmatic surface; `main()` is the `eval-extraction`
console script entrypoint, with `--dry-run` for cost-estimate-only.

The CLI variant registry (label → Variant resolution) is deferred until ≥2
candidate variants exist; live mode prints a clear redirect to the workbench
notebook. `--dry-run` is the load-bearing operator surface for Step 3.
"""

import argparse
import secrets
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from evals.core import (
    FixtureRun,
    RunRecord,
    RunStatus,
    ScoreReport,
    Variant,
    load_fixtures,
    save_run,
)
from evals.extraction.types import ExtractionFixture
from evals.extraction.workbench import run_variant

_DEFAULT_DATA_ROOT = Path("data/eval_runs")
_RECORD_VERSION = "v1"


class ScorerProtocol(Protocol):
    def score(self, *, expected: dict[str, Any], actual: dict[str, Any]) -> Any: ...


def run_benchmark(
    *,
    variant: Variant,
    fixtures: Sequence[ExtractionFixture],
    scorer: ScorerProtocol,
    fixture_set: str = "<inline>",
    data_root: Path | None = None,
    persist: bool = True,
) -> RunRecord:
    """Run a variant over fixtures, score each, aggregate per-field + per-content-type."""
    started = datetime.now(UTC).isoformat()
    samples: list[FixtureRun] = []
    per_field_acc: dict[str, list[float]] = defaultdict(list)
    per_type_overall: dict[str, list[float]] = defaultdict(list)
    for fix in fixtures:
        fr = run_variant(variant, fix)
        samples.append(fr)
        if fr.status != RunStatus.SUCCESS or fr.output is None:
            continue
        actual_card = fr.output.get("topic_card", {})
        score = scorer.score(expected=fix.expected_topic_card, actual=actual_card)
        for field, v in score.value.items():
            per_field_acc[field].append(v)
        per_type_overall[fix.content_type].append(score.value.get("__overall__", 0.0))
    completed = datetime.now(UTC).isoformat()

    metrics = {field: sum(vs) / len(vs) for field, vs in per_field_acc.items() if vs}
    stratifications = {
        "by_content_type": {ct: sum(vs) / len(vs) for ct, vs in per_type_overall.items() if vs}
    }
    sr = ScoreReport(
        scorer_name="TopicCardScorer",
        metrics=metrics,
        stratifications=stratifications,
        sample_count=len(samples),
    )
    rec = RunRecord(
        run_id=_ulid(),
        kind="benchmark",
        target="extraction",
        variant_name=variant.name,
        variant_config=variant.config,
        variant_provenance=variant.provenance,
        fixture_set=fixture_set,
        fixture_anchor=None,
        started_at=started,
        completed_at=completed,
        samples=samples,
        scores=[sr],
        config={},
    )
    if persist:
        root = Path(data_root) if data_root is not None else _DEFAULT_DATA_ROOT
        save_run(root=root, version=_RECORD_VERSION, record=rec)
    return rec


def dry_run_estimate(
    *,
    n_fixtures: int,
    n_calls_per_fixture: int = 3,
    avg_tokens_per_call: int = 2500,
    cost_per_1k_tokens: float = 0.005,
) -> float:
    total_tokens = n_fixtures * n_calls_per_fixture * avg_tokens_per_call
    return (total_tokens / 1000) * cost_per_1k_tokens


def main(argv: Sequence[str] | None = None) -> int:
    """`eval-extraction <variant-label> --fixtures path/to/extraction_eval.jsonl [--dry-run]`."""
    p = argparse.ArgumentParser(prog="eval-extraction")
    p.add_argument("variant_label", help="Variant name (e.g. v5_baseline)")
    p.add_argument("--fixtures", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-cost-usd", type=float, default=5.0)
    args = p.parse_args(argv)

    _header, rows = load_fixtures(args.fixtures, expected_schema_version=1)

    if args.dry_run:
        est = dry_run_estimate(n_fixtures=len(rows))
        print(f"Estimated: {len(rows)} fixtures × 3 calls × 2500 tokens ≈ ${est:.2f}")
        return 0

    print(
        f"Variant '{args.variant_label}' resolution not yet wired into the CLI. "
        f"Use the workbench notebook to run variants in Step 3. "
        f"CLI variant registry lands as a follow-up.",
        file=sys.stderr,
    )
    return 2


def _ulid() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
