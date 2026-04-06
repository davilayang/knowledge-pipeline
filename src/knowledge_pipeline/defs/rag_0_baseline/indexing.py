# Asset: indexed_contents — upsert pre-computed embeddings to ChromaDB.

from knowledge_pipeline.config import get_strategy
from knowledge_pipeline.defs.shared.op_factories import create_indexing_asset

_CFG = get_strategy("rag_0_baseline")

indexed_contents = create_indexing_asset(
    strategy_name=_CFG["strategy_name"],
    collection_name=_CFG["collection_name"],
    embedding_model=_CFG["embedding_model"],
    group_name="rag_0_baseline",
    deps=["embedded_contents"],
    asset_name="indexed_contents",
)
