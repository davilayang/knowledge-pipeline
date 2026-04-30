import dagster as dg

from .raw_store import raw_store_copy
from .resources import RawStoreResource, StrategyPathsResource, VectorStoreResource

shared_resources = {
    "raw_store": RawStoreResource(),
    "vector_store": VectorStoreResource(),
    "strategy_paths": StrategyPathsResource(),
}

defs = dg.Definitions(
    assets=[raw_store_copy],
    resources=shared_resources,
)
