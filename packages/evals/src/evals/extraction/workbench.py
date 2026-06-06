"""Workbench helpers for the extraction harness.

Wraps `evals.core` run primitives for notebook-friendly single-fixture and
multi-variant calls. Persists `RunRecord` under
`{data_root}/workbench/extraction/v1/{run_id}/run.json` (Inspect-shaped via
`evals.core.runs.save_run`).
"""

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from evals.core import (
    CostBudget,
    FixtureRun,
    RunRecord,
    Variant,
    save_run,
)
from evals.extraction.types import ExtractionFixture

_DEFAULT_DATA_ROOT = Path("data/eval_runs")
_RECORD_VERSION = "v1"


def run_variant(variant: Variant, fixture: ExtractionFixture) -> FixtureRun:
    """Run one variant against one fixture; return FixtureRun. Does NOT persist."""
    return variant.run(fixture)


def run_variants(
    variants: list[Variant],
    fixtures: Iterable[ExtractionFixture],
    *,
    budget: CostBudget,
    fixture_set: str = "<inline>",
    data_root: Path | None = None,
    persist: bool = True,
    estimated_usd: float | None = None,
) -> list[RunRecord]:
    """Run each variant against the fixture set; return one RunRecord per variant.

    If `estimated_usd` is supplied, the budget gate fires before any run starts
    (raises `BudgetExceededError`). Without an estimate the budget is recorded
    in RunRecord.config but not enforced — runtime per-fixture cost would only
    be known after the run, defeating the gate's purpose.
    """
    if estimated_usd is not None:
        budget.check_estimate(estimated_usd=estimated_usd)
    root = Path(data_root) if data_root is not None else _DEFAULT_DATA_ROOT
    fixtures = list(fixtures)
    records: list[RunRecord] = []
    for v in variants:
        started = datetime.now(UTC).isoformat()
        samples = [run_variant(v, f) for f in fixtures]
        completed = datetime.now(UTC).isoformat()
        rec = RunRecord(
            run_id=_ulid(),
            kind="workbench",
            target="extraction",
            variant_name=v.name,
            variant_config=v.config,
            variant_provenance=v.provenance,
            fixture_set=fixture_set,
            fixture_anchor=None,
            started_at=started,
            completed_at=completed,
            samples=samples,
            scores=[],
            config={
                "budget": {
                    "max_concurrent_calls": budget.max_concurrent_calls,
                    "max_cost_usd_per_run": budget.max_cost_usd_per_run,
                }
            },
        )
        if persist:
            save_run(root=root, version=_RECORD_VERSION, record=rec)
        records.append(rec)
    return records


def _ulid() -> str:
    """Lightweight monotonic ID; not a true ULID, but stable + sortable enough."""
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
