# Asset: indexed_contents — upsert pre-computed embeddings to ChromaDB.

from knowledge_pipeline.defs.shared.op_factories import create_indexing_asset

from .config import COLLECTION_NAME, EMBEDDING_MODEL, STRATEGY_NAME

indexed_contents = create_indexing_asset(
    strategy_name=STRATEGY_NAME,
    collection_name=COLLECTION_NAME,
    embedding_model=EMBEDDING_MODEL,
    group_name="rag_0_baseline",
    deps=["embedded_contents"],
    asset_name="indexed_contents",
)
