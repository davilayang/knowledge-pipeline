import dagster as dg

from knowledge_pipeline.defs.shared.resources import StrategyPathsResource

from .assets import bge_chunked, bge_embedded, bge_indexed, raw_store_copy
from .resources import VectorStoreResource

index_contents_job = dg.define_asset_job(
    name="rag_1_bge",
    selection=[raw_store_copy, bge_chunked, bge_embedded, bge_indexed],
    description="BGE RAG: markdown chunking + BGE-small-en-v1.5 embedding + cosine retrieval",
)

defs = dg.Definitions(
    assets=[raw_store_copy, bge_chunked, bge_embedded, bge_indexed],
    jobs=[index_contents_job],
    resources={
        "vector_store": VectorStoreResource(),
        "strategy_paths": StrategyPathsResource(strategy_name="rag_1_bge"),
    },
)
