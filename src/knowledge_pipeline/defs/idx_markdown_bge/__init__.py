import dagster as dg

from .assets import bge_chunked, bge_embedded, bge_indexed, raw_store_copy

index_contents_job = dg.define_asset_job(
    name="idx_markdown_bge",
    selection=[raw_store_copy, bge_chunked, bge_embedded, bge_indexed],
    description="BGE index: markdown chunking + BGE-small-en-v1.5 embedding",
)

defs = dg.Definitions(
    assets=[raw_store_copy, bge_chunked, bge_embedded, bge_indexed],
    jobs=[index_contents_job],
)
