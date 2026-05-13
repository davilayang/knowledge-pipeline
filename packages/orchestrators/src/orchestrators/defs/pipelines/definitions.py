# Pipelines code location — scheduled production jobs.
# Each sub-Definitions registers its own resources. No shared resource is
# wired here today; Phase D's populate_vector_store adds the live Chroma
# resource at its own definitions level.

import dagster as dg

from . import backup_readings, synthesize_wiki, upstream_sources

defs = dg.Definitions.merge(
    upstream_sources.defs,
    backup_readings.defs,
    synthesize_wiki.defs,
)
