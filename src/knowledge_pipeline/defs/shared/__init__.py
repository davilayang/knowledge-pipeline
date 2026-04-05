import dagster as dg

from .raw_store import raw_store_copy
from .resources import RawStoreResource

defs = dg.Definitions(
    assets=[raw_store_copy],
    resources={
        "raw_store": RawStoreResource(),
    },
)
