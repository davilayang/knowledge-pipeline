# Semantic MiniLM index assets — uses shared op factories with semantic chunking config.

import dagster as dg

from knowledge_pipeline.defs.shared.op_factories import (
    create_chunk_batch_op,
    create_embed_batch_op,
    create_indexing_asset,
    create_load_chunked_items_op,
    fan_out_batches,
    fetch_pending,
    gather_ids,
)
from knowledge_pipeline.defs.shared.raw_store import raw_store_copy
from knowledge_pipeline.lib.utils import get_strategy

_CFG = get_strategy("idx_semantic_minilm")

chunk_batch = create_chunk_batch_op(
    _CFG["strategy_name"], _CFG["chunking"], _CFG["chunk_size"], _CFG["chunk_overlap"]
)
load_chunked_items = create_load_chunked_items_op(_CFG["strategy_name"])
embed_batch = create_embed_batch_op(
    _CFG["strategy_name"], _CFG["collection_name"], _CFG["embedding_model"]
)

semantic_minilm_indexed = create_indexing_asset(
    strategy_name=_CFG["strategy_name"],
    collection_name=_CFG["collection_name"],
    embedding_model=_CFG["embedding_model"],
    group_name="idx_semantic_minilm",
    deps=["semantic_minilm_embedded"],
    asset_name="semantic_minilm_indexed",
)

__all__ = [
    "raw_store_copy",
    "semantic_minilm_chunked",
    "semantic_minilm_embedded",
    "semantic_minilm_indexed",
]


@dg.graph_asset(
    group_name="idx_semantic_minilm",
    description="Chunk pending content (semantic splitting by embedding similarity)",
    ins={"raw_store_snapshot": dg.AssetIn(key="raw_store_copy")},
)
def semantic_minilm_chunked(raw_store_snapshot) -> list[str]:
    items = fetch_pending(raw_store_snapshot=raw_store_snapshot)
    batches = fan_out_batches(items)
    per_batch = batches.map(chunk_batch)
    return gather_ids(per_batch.collect())


@dg.graph_asset(
    group_name="idx_semantic_minilm",
    description="Compute MiniLM embeddings for semantically chunked content",
    ins={"chunks_ready": dg.AssetIn(key="semantic_minilm_chunked")},
)
def semantic_minilm_embedded(chunks_ready) -> list[str]:
    items = load_chunked_items(chunks_ready=chunks_ready)
    batches = fan_out_batches(items)
    per_batch = batches.map(embed_batch)
    return gather_ids(per_batch.collect())
