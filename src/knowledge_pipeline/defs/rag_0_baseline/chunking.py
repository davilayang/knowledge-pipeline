# Graph asset: chunked_contents — fetch pending items, chunk in batches.

import logging

import dagster as dg

from knowledge_pipeline.lib.chunking import chunk_markdown
from knowledge_pipeline.lib.store import get_contents, set_vector_status

from .resources import RawStoreResource

logger = logging.getLogger(__name__)

BATCH_SIZE = 3


class FetchConfig(dg.Config):
    """Runtime config for fetch_pending. Override max_items in the Launchpad for dev."""

    max_items: int = 0  # 0 = no limit (prod default)


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


@dg.op(ins={"raw_store_snapshot": dg.In(dagster_type=dg.Nothing)})
def fetch_pending(config: FetchConfig, raw_store: RawStoreResource) -> list[dict]:
    """Query pending/ready items from raw_store, return as serializable dicts."""
    db_path = raw_store.get_path()
    items = []
    for status in ["pending", "ready"]:
        items.extend(get_contents(vector_status=status, db_path=db_path))

    result = []
    for item in items:
        if not item.content_md or len(item.content_md.strip()) < 50:
            logger.warning("Skipping %s — content too short", item.content_id)
            set_vector_status(item.content_id, "skip", db_path=raw_store.get_source_path())
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


@dg.op(out=dg.DynamicOut())
def fan_out_chunk_batches(context: dg.OpExecutionContext, items: list[dict]):
    """Split items into batches and yield as DynamicOutputs."""
    if not items:
        context.log.info("No items to chunk.")
        return
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i : i + BATCH_SIZE]
        yield dg.DynamicOutput(batch, mapping_key=f"batch_{i}")


@dg.op
def chunk_batch(context: dg.OpExecutionContext, batch: list[dict]) -> list[dict]:
    """Chunk each item in the batch. Returns list of chunked item dicts."""
    results = []
    for item in batch:
        try:
            chunks = chunk_markdown(item["content_md"])
            if not chunks:
                continue
            results.append(
                {
                    "content_id": item["content_id"],
                    "title": item["title"],
                    "author": item["author"],
                    "url": item["url"],
                    "source_key": item["source_key"],
                    "content_date": item["content_date"],
                    "chunks": [
                        {"text": c.text, "heading": c.heading, "index": c.index} for c in chunks
                    ],
                }
            )
        except Exception as exc:
            logger.error("Failed to chunk %s: %s", item["content_id"], exc)
    context.log.info("Chunked %d items in batch", len(results))
    return results


@dg.op
def gather_chunks(results: list[list[dict]]) -> list[dict]:
    """Flatten batched results. Required wrapper — .collect() can't be returned directly."""
    return [item for batch in results for item in batch]


# ---------------------------------------------------------------------------
# Graph asset
# ---------------------------------------------------------------------------


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Chunk pending content into structured records with batched fan-out",
    ins={"raw_store_snapshot": dg.AssetIn(key="raw_store_copy")},
)
def chunked_contents(raw_store_snapshot) -> list[dict]:
    items = fetch_pending(raw_store_snapshot=raw_store_snapshot)
    batches = fan_out_chunk_batches(items)
    per_batch = batches.map(chunk_batch)
    return gather_chunks(per_batch.collect())
