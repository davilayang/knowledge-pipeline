# Graph asset: embedded_contents — read chunk JSONs, compute embeddings,
# write results to strategy's embeddings directory.

import dagster as dg

from knowledge_pipeline.config import get_strategy
from knowledge_pipeline.defs.shared.op_factories import (
    create_embed_batch_op,
    create_load_chunked_items_op,
    fan_out_embed_batches,
    gather_embed_ids,
)

_CFG = get_strategy("rag_0_baseline")

# Strategy-specific op instances
load_chunked_items = create_load_chunked_items_op(_CFG["strategy_name"])
embed_batch = create_embed_batch_op(
    _CFG["strategy_name"], _CFG["collection_name"], _CFG["embedding_model"]
)


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Compute embeddings for chunked content and write to strategy embeddings directory",
    ins={"chunks_ready": dg.AssetIn(key="chunked_contents")},
)
def embedded_contents(chunks_ready) -> list[str]:
    items = load_chunked_items(chunks_ready=chunks_ready)
    batches = fan_out_embed_batches(items)
    per_batch = batches.map(embed_batch)
    return gather_embed_ids(per_batch.collect())
