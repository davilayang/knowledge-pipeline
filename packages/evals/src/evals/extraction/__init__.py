"""evals.extraction — first per-pipeline harness on top of evals.core.

Public surface:
  Variants:    make_three_call_variant
  Scorers:     TopicCardScorer, NarrativeCoverageScorer
  Workbench:   run_variant, run_variants
  Benchmark:   run_benchmark
  Types:       ExtractionFixture, TopicCardFields, ExtractionDiffReport
"""

from evals.extraction.benchmark import run_benchmark
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
    "make_three_call_variant",
    "run_benchmark",
    "run_variant",
    "run_variants",
]
