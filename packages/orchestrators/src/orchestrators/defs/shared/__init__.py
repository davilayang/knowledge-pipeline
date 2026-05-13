import dagster as dg

from .raw_store import raw_store_copy
from .resources import RawStoreResource, VectorStoreResource  # noqa: F401

shared_resources = {
    "raw_store": RawStoreResource(),
}

defs = dg.Definitions(
    assets=[raw_store_copy],
    resources=shared_resources,
)
