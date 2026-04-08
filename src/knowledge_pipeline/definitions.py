# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Each defs/ subfolder exports its own Definitions; this file merges them.

import dagster as dg

from knowledge_pipeline.defs import shared
from knowledge_pipeline.defs.pipelines import backup_databases
from knowledge_pipeline.defs.workbench import (
    evaluate,
    idx_markdown_bge,
    idx_markdown_minilm,
    idx_recursive_minilm,
)

defs = dg.Definitions.merge(
    shared.defs,
    # workbench (manually triggered)
    idx_markdown_minilm.defs,
    idx_markdown_bge.defs,
    idx_recursive_minilm.defs,
    evaluate.defs,
    # pipelines (scheduled)
    backup_databases.defs,
)
