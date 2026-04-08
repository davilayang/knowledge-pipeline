# Asset: baseline_indexed — upsert pre-computed embeddings to ChromaDB.

from knowledge_pipeline.defs.shared.op_factories import create_indexing_asset
from knowledge_pipeline.lib.utils import get_strategy

_CFG = get_strategy("idx_markdown_minilm")

baseline_indexed = create_indexing_asset(
    strategy_name=_CFG["strategy_name"],
    collection_name=_CFG["collection_name"],
    embedding_model=_CFG["embedding_model"],
    group_name="idx_markdown_minilm",
    deps=["baseline_embedded"],
    asset_name="baseline_indexed",
)
