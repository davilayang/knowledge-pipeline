"""Scored aggregation for the extraction harness.

`run_benchmark` is the extraction-facing programmatic surface — a thin wrapper
over the shared `evals.core.run_and_report` runner that binds `target="extraction"`
and `ExtractionFixture` typing. Topic-card A/B iteration runs through the
workbench notebooks (`run_variants`); narrative-coverage scoring runs through the
`eval-narrative-coverage` CLI.
"""

from collections.abc import Sequence
from pathlib import Path

from evals.core import RunRecord, Variant
from evals.core.harness import ScorerProtocol, run_and_report
from evals.extraction.types import ExtractionFixture

__all__ = ["ScorerProtocol", "run_benchmark"]


def run_benchmark(
    *,
    variant: Variant,
    fixtures: Sequence[ExtractionFixture],
    scorer: ScorerProtocol,
    fixture_set: str = "<inline>",
    data_root: Path | None = None,
    persist: bool = True,
) -> RunRecord:
    """Run a variant over fixtures, score each, aggregate per-field + per-content-type.

    Thin extraction-facing wrapper over the shared `run_and_report` runner.
    """
    return run_and_report(
        variant=variant,
        fixtures=fixtures,
        scorer=scorer,
        target="extraction",
        fixture_set=fixture_set,
        data_root=data_root,
        persist=persist,
    )
