# populate_vector_store — embed pending items from each source into ChromaDB.
# Lands paused (default_status=STOPPED). See README.md for the runbook.

import dagster as dg

from orchestrators.defs.shared.resources import VectorStoreResource

from .assets import all_assets
from .resources import SourcesResource
from .schedules import populate_vector_store_job, run_populate_vector_store


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "sources": SourcesResource(backup_source_dir=dg.EnvVar("BACKUP_SOURCE_DIR")),
        "vector_store": VectorStoreResource(
            chroma_host=dg.EnvVar("CHROMA_HOST"),
            chroma_port=dg.EnvVar.int("CHROMA_PORT"),
        ),
    }


defs = dg.Definitions(
    assets=all_assets,
    jobs=[populate_vector_store_job],
    schedules=[run_populate_vector_store],
    resources=build_resources(),
)
