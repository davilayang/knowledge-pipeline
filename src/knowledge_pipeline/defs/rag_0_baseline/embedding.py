# Graph asset: baseline_embedded — read chunk JSONs, compute embeddings,
# write results to strategy's embeddings directory.

import dagster as dg

from knowledge_pipeline.defs.shared.op_factories import (
    create_embed_batch_op,
    create_load_chunked_items_op,
    fan_out_batches,
    gather_ids,
)
from knowledge_pipeline.lib.utils import get_strategy

_CFG = get_strategy("rag_0_baseline")

load_chunked_items = create_load_chunked_items_op(_CFG["strategy_name"])
embed_batch = create_embed_batch_op(
    _CFG["strategy_name"], _CFG["collection_name"], _CFG["embedding_model"]
)


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Compute embeddings for chunked content and write to strategy embeddings directory",
    ins={"chunks_ready": dg.AssetIn(key="baseline_chunked")},
)
def baseline_embedded(chunks_ready) -> list[str]:
    items = load_chunked_items(chunks_ready=chunks_ready)
    batches = fan_out_batches(items)
    per_batch = batches.map(embed_batch)
    return gather_ids(per_batch.collect())
