# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Each defs/ subfolder exports its own Definitions; this file merges them.

import dagster as dg

from knowledge_pipeline.defs import backup_databases, evaluate, rag_0_baseline, shared

defs = dg.Definitions.merge(shared.defs, rag_0_baseline.defs, backup_databases.defs, evaluate.defs)
