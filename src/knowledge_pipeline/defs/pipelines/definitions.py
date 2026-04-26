# Pipelines code location — scheduled production jobs.

import dagster as dg

from knowledge_pipeline.defs.shared import shared_resources

from . import backup_databases, wiki

defs = dg.Definitions.merge(
    dg.Definitions(resources=shared_resources),
    backup_databases.defs,
    wiki.defs,
)
