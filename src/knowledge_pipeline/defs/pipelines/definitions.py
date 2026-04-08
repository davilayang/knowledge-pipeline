# Pipelines code location — scheduled production jobs.

import dagster as dg

from knowledge_pipeline.defs import shared

from . import backup_databases

defs = dg.Definitions.merge(
    shared.defs,
    backup_databases.defs,
)
