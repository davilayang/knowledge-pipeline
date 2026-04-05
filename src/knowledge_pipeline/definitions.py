# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Each defs/ subfolder exports its own Definitions; this file merges them.

import dagster as dg

from knowledge_pipeline.defs import backup_databases, evaluate, rag_0_baseline

defs = dg.Definitions.merge(rag_0_baseline.defs, backup_databases.defs, evaluate.defs)
