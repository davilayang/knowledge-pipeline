# Pipelines code location — scheduled production jobs.

import dagster as dg

from orchestrators.defs.shared import shared_resources

from . import backup_readings, wiki

defs = dg.Definitions.merge(
    dg.Definitions(resources=shared_resources),
    backup_readings.defs,
    wiki.defs,
)
