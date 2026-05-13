# Top-level Dagster Definitions — entrypoint for `dagster dev`.

import dagster as dg

from orchestrators.defs import shared
from orchestrators.defs.pipelines import backup_readings, synthesize_wiki, upstream_sources

defs = dg.Definitions.merge(
    shared.defs,
    upstream_sources.defs,
    backup_readings.defs,
    synthesize_wiki.defs,
)
