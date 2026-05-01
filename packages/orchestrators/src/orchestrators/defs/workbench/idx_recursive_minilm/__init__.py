import dagster as dg

from .assets import (
    raw_store_copy,
    recursive_minilm_chunked,
    recursive_minilm_embedded,
    recursive_minilm_indexed,
)

index_contents_job = dg.define_asset_job(
    name="idx_recursive_minilm",
    selection=[
        raw_store_copy,
        recursive_minilm_chunked,
        recursive_minilm_embedded,
        recursive_minilm_indexed,
    ],
    description="Recursive index: recursive character chunking + MiniLM embedding",
)

defs = dg.Definitions(
    assets=[
        raw_store_copy,
        recursive_minilm_chunked,
        recursive_minilm_embedded,
        recursive_minilm_indexed,
    ],
    jobs=[index_contents_job],
)
