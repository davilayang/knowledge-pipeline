# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Each defs/ subfolder exports its own Definitions; this file merges them.

import dagster as dg

from knowledge_pipeline.defs import (
    backup_databases,
    evaluate,
    idx_markdown_bge,
    idx_markdown_minilm,
    idx_recursive_minilm,
    shared,
)

defs = dg.Definitions.merge(
    shared.defs,
    idx_markdown_minilm.defs,
    idx_markdown_bge.defs,
    idx_recursive_minilm.defs,
    backup_databases.defs,
    evaluate.defs,
)
