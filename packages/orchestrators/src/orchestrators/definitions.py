# Dagster code location — the entry point loaded by `dagster dev` and the
# production gRPC server. Each sub-Definitions registers its own resources.

import dagster as dg

from orchestrators.defs import (
    backup_readings,
    fetch_extract_queue,
    populate_vector_store,
    shared,
    synthesize_wiki,
    triage_knowledge_queue,
    upstream_sources,
)

defs = dg.Definitions.merge(
    shared.defs,
    upstream_sources.defs,
    backup_readings.defs,
    synthesize_wiki.defs,
    populate_vector_store.defs,
    triage_knowledge_queue.defs,
    fetch_extract_queue.defs,
)
