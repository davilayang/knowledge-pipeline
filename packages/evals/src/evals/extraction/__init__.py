"""evals.extraction — first per-pipeline harness on top of evals.core.

Public surface:
  Variants:    make_three_call_variant
  Scorers:     TopicCardScorer, NarrativeCoverageScorer
  Workbench:   run_variant, run_variants
  Benchmark:   run_benchmark, dry_run_estimate, main
  Types:       ExtractionFixture, TopicCardFields, ExtractionDiffReport
"""

from evals.extraction.benchmark import dry_run_estimate, run_benchmark
from evals.extraction.scorers import NarrativeCoverageScorer, TopicCardScorer
from evals.extraction.types import (
    ExtractionDiffReport,
    ExtractionFixture,
    TopicCardFields,
)
from evals.extraction.variants import make_three_call_variant
from evals.extraction.workbench import run_variant, run_variants

__all__ = [
    "ExtractionDiffReport",
    "ExtractionFixture",
    "NarrativeCoverageScorer",
    "TopicCardFields",
    "TopicCardScorer",
    "dry_run_estimate",
    "make_three_call_variant",
    "run_benchmark",
    "run_variant",
    "run_variants",
]
