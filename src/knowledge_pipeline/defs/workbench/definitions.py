# Workbench code location — manually triggered index & eval jobs.

import dagster as dg

from knowledge_pipeline.defs import shared

from . import evaluate, idx_markdown_bge, idx_markdown_minilm, idx_recursive_minilm

defs = dg.Definitions.merge(
    shared.defs,
    idx_markdown_minilm.defs,
    idx_markdown_bge.defs,
    idx_recursive_minilm.defs,
    evaluate.defs,
)
