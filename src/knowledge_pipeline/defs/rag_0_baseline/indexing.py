# Graph asset: indexed_contents — embed chunked content and upsert to ChromaDB.

import logging

import dagster as dg

from knowledge_pipeline.lib.store import set_vector_status

from .chunking import BATCH_SIZE
from .resources import RawStoreResource, VectorStoreResource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


@dg.op(out=dg.DynamicOut())
def fan_out_embed_batches(context: dg.OpExecutionContext, chunked_items: list[dict]):
    """Split chunked items into batches for embedding."""
    if not chunked_items:
        context.log.info("No items to embed.")
        return
    for i in range(0, len(chunked_items), BATCH_SIZE):
        batch = chunked_items[i : i + BATCH_SIZE]
        yield dg.DynamicOutput(batch, mapping_key=f"batch_{i}")


@dg.op
def embed_batch(
    context: dg.OpExecutionContext,
    batch: list[dict],
    vector_store: VectorStoreResource,
) -> list[dict]:
    """Embed and upsert a batch of chunked items into ChromaDB."""
    collection = vector_store.get_collection()
    results = []

    for item in batch:
        chunks = item["chunks"]
        metadata_base: dict = {
            "title": item["title"],
            "author": item["author"],
            "content_date": item["content_date"],
        }
        if item["url"]:
            metadata_base["url"] = item["url"]

        # Delete pre-existing chunks for this content_id
        existing = collection.get(where={"content_id": item["content_id"]})
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        ids = [f"{item['content_id']}::chunk{c['index']}" for c in chunks]
        # Prepend title/author context for embedding disambiguation
        documents = [
            f"Title: {item['title']} | Author: {item['author']}\n{c['text']}" for c in chunks
        ]
        metadatas = [
            {
                **metadata_base,
                "content_id": item["content_id"],
                "chunk_index": c["index"],
                "heading": c["heading"],
            }
            for c in chunks
        ]

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)  # type: ignore[arg-type]
        results.append(
            {
                "content_id": item["content_id"],
                "title": item["title"][:60],
                "source": item["source_key"],
                "chunks": len(chunks),
                "status": "indexed",
            }
        )

    context.log.info("Embedded %d items in batch", len(results))
    return results


@dg.op
def finalize(
    context: dg.OpExecutionContext,
    results: list[list[dict]],
    raw_store: RawStoreResource,
) -> dict:
    """Update vector_status in source DB and build summary. Wrapper for .collect()."""
    all_results = [item for batch in results for item in batch]
    source_db_path = raw_store.get_source_path()

    indexed_count = 0
    error_count = 0
    total_chunks = 0
    details: list[dict] = []

    for item in all_results:
        try:
            set_vector_status(item["content_id"], "indexed", db_path=source_db_path)
            indexed_count += 1
            total_chunks += item["chunks"]
            details.append(item)
        except Exception as exc:
            logger.error("Failed to finalize %s: %s", item["content_id"], exc)
            set_vector_status(item["content_id"], "error", db_path=source_db_path)
            error_count += 1

    context.log.info("Finalized: %d indexed, %d errors", indexed_count, error_count)
    return {
        "indexed": indexed_count,
        "errors": error_count,
        "total_chunks": total_chunks,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Graph asset
# ---------------------------------------------------------------------------


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Embed chunked content and upsert to ChromaDB with batched fan-out",
)
def indexed_contents(chunked_contents: list[dict]) -> dict:
    batches = fan_out_embed_batches(chunked_contents)
    per_batch = batches.map(embed_batch)
    return finalize(per_batch.collect())
