# BGE index assets — uses shared op factories with BGE-specific config.

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

_CFG = get_strategy("idx_markdown_bge")

chunk_batch = create_chunk_batch_op(
    _CFG["strategy_name"], _CFG["chunking"], _CFG["chunk_size"], _CFG["chunk_overlap"]
)
load_chunked_items = create_load_chunked_items_op(_CFG["strategy_name"])
embed_batch = create_embed_batch_op(
    _CFG["strategy_name"], _CFG["collection_name"], _CFG["embedding_model"]
)

bge_indexed = create_indexing_asset(
    strategy_name=_CFG["strategy_name"],
    collection_name=_CFG["collection_name"],
    embedding_model=_CFG["embedding_model"],
    group_name="idx_markdown_bge",
    deps=["bge_embedded"],
    asset_name="bge_indexed",
)

__all__ = ["raw_store_copy", "bge_chunked", "bge_embedded", "bge_indexed"]


@dg.graph_asset(
    group_name="idx_markdown_bge",
    description="Chunk pending content (BGE strategy)",
    ins={"raw_store_snapshot": dg.AssetIn(key="raw_store_copy")},
)
def bge_chunked(raw_store_snapshot) -> list[str]:
    items = fetch_pending(raw_store_snapshot=raw_store_snapshot)
    batches = fan_out_batches(items)
    per_batch = batches.map(chunk_batch)
    return gather_ids(per_batch.collect())


@dg.graph_asset(
    group_name="idx_markdown_bge",
    description="Compute BGE-small-en-v1.5 embeddings for chunked content",
    ins={"chunks_ready": dg.AssetIn(key="bge_chunked")},
)
def bge_embedded(chunks_ready) -> list[str]:
    items = load_chunked_items(chunks_ready=chunks_ready)
    batches = fan_out_batches(items)
    per_batch = batches.map(embed_batch)
    return gather_ids(per_batch.collect())
