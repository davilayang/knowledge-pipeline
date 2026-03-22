# src/knowledge_pipeline/definitions.py
# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Each defs/ subfolder exports its own Definitions; this file merges them.

import dagster as dg

from knowledge_pipeline.defs.backup_databases import defs as backup_defs
from knowledge_pipeline.defs.index_contents import defs as index_defs

defs = dg.Definitions.merge(index_defs, backup_defs)
