# Graph asset: baseline_chunked — fetch pending items, chunk in batches,
# write results to JSON files in the strategy's chunks directory.

import dagster as dg

from knowledge_pipeline.defs.shared.op_factories import (
    create_chunk_batch_op,
    fan_out_batches,
    fetch_pending,
    gather_ids,
)
from knowledge_pipeline.lib.utils import get_strategy

_CFG = get_strategy("idx_markdown_minilm")

chunk_batch = create_chunk_batch_op(
    _CFG["strategy_name"], _CFG["chunking"], _CFG["chunk_size"], _CFG["chunk_overlap"]
)


@dg.graph_asset(
    group_name="idx_markdown_minilm",
    description="Chunk pending content and write to JSON files in strategy chunks directory",
    ins={"raw_store_snapshot": dg.AssetIn(key="raw_store_copy")},
)
def baseline_chunked(raw_store_snapshot) -> list[str]:
    items = fetch_pending(raw_store_snapshot=raw_store_snapshot)
    batches = fan_out_batches(items)
    per_batch = batches.map(chunk_batch)
    return gather_ids(per_batch.collect())
