# Pipelines code location — scheduled production jobs.
# Each sub-Definitions registers its own resources; shared_resources (which
# pulls in chromadb/retrievers) is intentionally not imported here so this
# code location can run without the workbench optional deps installed.

import dagster as dg

from . import backup_readings, synthesize_wiki

defs = dg.Definitions.merge(
    backup_readings.defs,
    synthesize_wiki.defs,
)
