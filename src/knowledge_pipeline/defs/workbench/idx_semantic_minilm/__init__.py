import dagster as dg

from .assets import (
    raw_store_copy,
    semantic_minilm_chunked,
    semantic_minilm_embedded,
    semantic_minilm_indexed,
)

index_contents_job = dg.define_asset_job(
    name="idx_semantic_minilm",
    selection=[
        raw_store_copy,
        semantic_minilm_chunked,
        semantic_minilm_embedded,
        semantic_minilm_indexed,
    ],
    description="Semantic index: semantic chunking + MiniLM embedding",
)

defs = dg.Definitions(
    assets=[
        raw_store_copy,
        semantic_minilm_chunked,
        semantic_minilm_embedded,
        semantic_minilm_indexed,
    ],
    jobs=[index_contents_job],
)
