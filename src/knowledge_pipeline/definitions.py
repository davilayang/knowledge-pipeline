# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Each defs/ subfolder exports its own Definitions; this file merges them.

import dagster as dg

from knowledge_pipeline.defs.backup_databases import defs as backup_defs
from knowledge_pipeline.defs.rag_0_baseline import defs as baseline_defs

defs = dg.Definitions.merge(baseline_defs, backup_defs)
