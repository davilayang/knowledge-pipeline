import dagster as dg

from .raw_store import raw_store_copy
from .resources import RawStoreResource, VectorStoreResource, WikiResource  # noqa: F401

# "wiki" is bound here (not in a single pipeline) so it survives the retirement
# of the raw synthesize_wiki pipeline; sync_wiki_curation + the synthesis assets
# consume this one binding at the top-level Definitions.merge.
shared_resources = {
    "raw_store": RawStoreResource(),
    "wiki": WikiResource(backup_dir=dg.EnvVar("BACKUP_DST_DIR")),
}

defs = dg.Definitions(
    assets=[raw_store_copy],
    resources=shared_resources,
)
