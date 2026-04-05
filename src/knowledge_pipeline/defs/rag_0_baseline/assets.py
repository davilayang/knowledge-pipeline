# Dagster assets for knowledge indexing: raw_store.db → ChromaDB.
#
# Pipeline: raw_store_copy → chunked_contents (graph_asset) → indexed_contents (graph_asset)
# Each graph_asset uses batched DynamicOut for per-batch visibility in the Dagster UI.

import logging
import sqlite3

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.config import DATA_DIR, SOURCE_RAW_STORE
from knowledge_pipeline.lib.chunking import chunk_markdown
from knowledge_pipeline.lib.store import get_contents, set_vector_status

from .resources import RawStoreResource, VectorStoreResource

logger = logging.getLogger(__name__)

ASSET_OWNERS = ["team:data-eng"]
ASSET_TAGS = {"domain": "knowledge"}
BATCH_SIZE = 10

# ---------------------------------------------------------------------------
# Shared asset: raw_store_copy
# ---------------------------------------------------------------------------


# TODO: Replace with live access with tunnel using datasette? or another way?
@dg.asset(
    group_name="rag_0_baseline",
    compute_kind="filesystem",
    owners=ASSET_OWNERS,
    tags=ASSET_TAGS,
    code_version="1",
    description="Copy raw_store.db from newsletter-assistant to local data/",
)
def raw_store_copy(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Copy the source database using SQLite backup API for a consistent snapshot."""
    source = SOURCE_RAW_STORE
    if not source.exists():
        raise FileNotFoundError(f"Source database not found: {source}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / "raw_store.db"

    src_conn = sqlite3.connect(source)
    dst_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    size = dest.stat().st_size
    context.log.info("Copied raw_store.db (%d bytes) to %s", size, dest)
    return dg.MaterializeResult(
        metadata={
            "size_bytes": dg.MetadataValue.int(size),
            "source": dg.MetadataValue.path(str(source)),
        }
    )


# ---------------------------------------------------------------------------
# Chunking ops
# ---------------------------------------------------------------------------


@dg.op(ins={"raw_store_snapshot": dg.In(dagster_type=dg.Nothing)})
def fetch_pending(raw_store: RawStoreResource) -> list[dict]:
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
# chunked_contents graph_asset
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


# ---------------------------------------------------------------------------
# Embedding/indexing ops
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
# indexed_contents graph_asset
# ---------------------------------------------------------------------------


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Embed chunked content and upsert to ChromaDB with batched fan-out",
)
def indexed_contents(chunked_contents: list[dict]) -> dict:
    batches = fan_out_embed_batches(chunked_contents)
    per_batch = batches.map(embed_batch)
    return finalize(per_batch.collect())
