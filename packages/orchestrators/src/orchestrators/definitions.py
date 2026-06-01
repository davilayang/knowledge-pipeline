# Dagster code location — the entry point loaded by `dagster dev` and the
# production gRPC server. Each sub-Definitions registers its own resources.

import dagster as dg

from orchestrators.defs import (
    backup_readings,
    extract_queued_items,
    populate_vector_store,
    shared,
    synthesize_wiki,
    upstream_sources,
)

defs = dg.Definitions.merge(
    shared.defs,
    upstream_sources.defs,
    backup_readings.defs,
    synthesize_wiki.defs,
    populate_vector_store.defs,
    extract_queued_items.defs,
)
