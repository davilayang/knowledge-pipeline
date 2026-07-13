"""evals.extraction — first per-pipeline harness on top of evals.core.

Public surface:
  Variants:    make_three_call_variant, make_wide_variant, openai_wide_extract_fn
  Scorers:     TopicCardScorer
  Grounding:   citable_units, number_source, verify_grounding (cite-by-index)
  Coverage:    coverage (index-decile localization + faithfulness, workbench-local)
  Layered:     make_layered_extract_fn, openai_chunk_extract_fn, chunk_units
               (chunk long source -> per-chunk extract -> merge, global indices)
  Workbench:   run_variant, run_variants
  Benchmark:   run_benchmark, dry_run_estimate, main
  Types:       ExtractionFixture, TopicCardFields, ExtractionDiffReport,
               WideOutput, Claim
"""

from evals.extraction.benchmark import dry_run_estimate, run_benchmark
from evals.extraction.coverage import coverage
from evals.extraction.layered import (
    Chunk,
    chunk_units,
    make_layered_extract_fn,
    openai_chunk_extract_fn,
    render_chunk,
)
from evals.extraction.scorers import TopicCardScorer
from evals.extraction.types import (
    ExtractionDiffReport,
    ExtractionFixture,
    TopicCardFields,
)
from evals.extraction.units import citable_units
from evals.extraction.variants import make_three_call_variant
from evals.extraction.verify import verify_grounding
from evals.extraction.wide import (
    WIDE_ITEMS_INSTRUCTION,
    Claim,
    WideOutput,
    make_wide_variant,
    number_source,
    openai_wide_extract_fn,
)
from evals.extraction.workbench import run_variant, run_variants

__all__ = [
    "WIDE_ITEMS_INSTRUCTION",
    "Chunk",
    "Claim",
    "ExtractionDiffReport",
    "ExtractionFixture",
    "TopicCardFields",
    "TopicCardScorer",
    "WideOutput",
    "chunk_units",
    "citable_units",
    "coverage",
    "dry_run_estimate",
    "make_layered_extract_fn",
    "make_three_call_variant",
    "make_wide_variant",
    "number_source",
    "openai_chunk_extract_fn",
    "openai_wide_extract_fn",
    "render_chunk",
    "run_benchmark",
    "run_variant",
    "run_variants",
    "verify_grounding",
]
