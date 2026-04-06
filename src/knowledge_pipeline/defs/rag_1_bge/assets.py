# BGE strategy assets — same ops as baseline, different asset names and group.

import dagster as dg

from knowledge_pipeline.defs.rag_0_baseline.chunking import (
    chunk_batch,
    fan_out_chunk_batches,
    fetch_pending,
    gather_chunk_ids,
)
from knowledge_pipeline.defs.rag_0_baseline.embedding import (
    embed_batch,
    fan_out_embed_batches,
    gather_embed_ids,
    load_chunked_items,
)
from knowledge_pipeline.defs.shared.raw_store import raw_store_copy

from .indexing import bge_indexed

# Re-export raw_store_copy so the job selection can reference it
__all__ = ["raw_store_copy", "bge_chunked", "bge_embedded", "bge_indexed"]


@dg.graph_asset(
    group_name="rag_1_bge",
    description="Chunk pending content (BGE strategy)",
    ins={"raw_store_snapshot": dg.AssetIn(key="raw_store_copy")},
)
def bge_chunked(raw_store_snapshot) -> list[str]:
    items = fetch_pending(raw_store_snapshot=raw_store_snapshot)
    batches = fan_out_chunk_batches(items)
    per_batch = batches.map(chunk_batch)
    return gather_chunk_ids(per_batch.collect())


@dg.graph_asset(
    group_name="rag_1_bge",
    description="Compute BGE-small-en-v1.5 embeddings for chunked content",
    ins={"chunks_ready": dg.AssetIn(key="bge_chunked")},
)
def bge_embedded(chunks_ready) -> list[str]:
    items = load_chunked_items(chunks_ready=chunks_ready)
    batches = fan_out_embed_batches(items)
    per_batch = batches.map(embed_batch)
    return gather_embed_ids(per_batch.collect())
