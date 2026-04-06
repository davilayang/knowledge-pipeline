# Graph asset: chunked_contents — fetch pending items, chunk in batches,
# write results to JSON files in the strategy's chunks directory.

import dagster as dg

from knowledge_pipeline.defs.shared.op_factories import (
    create_chunk_batch_op,
    fan_out_chunk_batches,
    gather_chunk_ids,
)
from knowledge_pipeline.defs.shared.resources import RawStoreResource
from knowledge_pipeline.lib.store import get_contents

from .config import STRATEGY_NAME

# Strategy-specific op instance
chunk_batch = create_chunk_batch_op(STRATEGY_NAME)


class FetchConfig(dg.Config):
    """Runtime config for fetch_pending. Override max_items in the Launchpad for dev."""

    max_items: int = 0  # 0 = no limit (prod default)


@dg.op(ins={"raw_store_snapshot": dg.In(dagster_type=dg.Nothing)})
def fetch_pending(config: FetchConfig, raw_store: RawStoreResource) -> list[dict]:
    """Fetch all content items with sufficient content for indexing."""
    db_path = raw_store.get_path()
    items = get_contents(db_path=db_path)

    result = []
    for item in items:
        if not item.content_md or len(item.content_md.strip()) < 50:
            continue
        result.append(
            {
                "content_id": item.content_id,
                "title": item.title,
                "author": item.author,
                "url": item.url,
                "source_key": item.source_key,
                "content_date": item.content_date.isoformat() if item.content_date else "",
                "content_md": item.content_md,
            }
        )

    if config.max_items > 0:
        result = result[: config.max_items]

    return result


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Chunk pending content and write to JSON files in strategy chunks directory",
    ins={"raw_store_snapshot": dg.AssetIn(key="raw_store_copy")},
)
def chunked_contents(raw_store_snapshot) -> list[str]:
    items = fetch_pending(raw_store_snapshot=raw_store_snapshot)
    batches = fan_out_chunk_batches(items)
    per_batch = batches.map(chunk_batch)
    return gather_chunk_ids(per_batch.collect())
