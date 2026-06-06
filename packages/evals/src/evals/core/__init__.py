"""Pure-function substrate for the eval / workbench layer.

See README.md for the full public API; this module re-exports the typed
records, variants, fixtures, runs, snapshotter, cost, and diff helpers.
Judges live under evals.core.judges to keep the top-level surface readable.
"""

from evals.core.cost import BudgetExceededError, CostBudget, CostEstimatorProtocol
from evals.core.diff import DiffReport, diff_runs, render_diff_html, render_diff_text
from evals.core.fixtures import FixtureHeader, SchemaVersionMismatch, load_fixtures, save_fixtures
from evals.core.runs import load_run, run_dir, save_run
from evals.core.snapshotter import snapshot
from evals.core.types import (
    FieldScore,
    FixtureRef,
    FixtureRun,
    RunRecord,
    RunStatus,
    ScoreReport,
    StageTrace,
    VariantProvenance,
)
from evals.core.variants import RetrievalVariant, Variant, corpus_signature, variant_identity

__all__ = [
    "BudgetExceededError",
    "CostBudget",
    "CostEstimatorProtocol",
    "DiffReport",
    "FieldScore",
    "FixtureHeader",
    "FixtureRef",
    "FixtureRun",
    "RetrievalVariant",
    "RunRecord",
    "RunStatus",
    "SchemaVersionMismatch",
    "ScoreReport",
    "StageTrace",
    "Variant",
    "VariantProvenance",
    "corpus_signature",
    "diff_runs",
    "load_fixtures",
    "load_run",
    "render_diff_html",
    "render_diff_text",
    "run_dir",
    "save_fixtures",
    "save_run",
    "snapshot",
    "variant_identity",
]
