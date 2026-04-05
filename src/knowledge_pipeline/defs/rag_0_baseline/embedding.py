# Graph asset: embedded_contents — read chunk JSONs, compute embeddings,
# write results to data/embeddings/.

import json
import logging

import dagster as dg

from knowledge_pipeline.config import CHUNKS_DIR, EMBEDDINGS_DIR

from .chunking import BATCH_SIZE
from .resources import VectorStoreResource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


@dg.op(ins={"chunks_ready": dg.In(dagster_type=dg.Nothing)})
def load_chunked_items(context: dg.OpExecutionContext) -> list[dict]:
    """Read all chunked item JSON files from data/chunks/."""
    if not CHUNKS_DIR.exists():
        context.log.warning("Chunks directory not found: %s", CHUNKS_DIR)
        return []
    items = []
    for path in sorted(CHUNKS_DIR.glob("*.json")):
        items.append(json.loads(path.read_text(encoding="utf-8")))
    context.log.info("Loaded %d chunked items from %s", len(items), CHUNKS_DIR)
    return items


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
) -> list[str]:
    """Compute embeddings for a batch and write to JSON files. Returns content_ids."""
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    collection = vector_store.get_collection()
    ef = collection._embedding_function  # noqa: SLF001
    content_ids = []

    for item in batch:
        chunks = item["chunks"]
        # Prepend title/author context for embedding disambiguation
        documents = [
            f"Title: {item['title']} | Author: {item['author']}\n{c['text']}" for c in chunks
        ]

        # Compute embeddings explicitly
        embeddings = ef(documents)

        metadata_base: dict = {
            "title": item["title"],
            "author": item["author"],
            "content_date": item["content_date"],
        }
        if item["url"]:
            metadata_base["url"] = item["url"]

        record = {
            "content_id": item["content_id"],
            "source_key": item["source_key"],
            "metadata_base": metadata_base,
            "chunks": [
                {
                    "id": f"{item['content_id']}::chunk{c['index']}",
                    "document": doc,
                    "embedding": [float(v) for v in emb],
                    "metadata": {
                        **metadata_base,
                        "content_id": item["content_id"],
                        "chunk_index": c["index"],
                        "heading": c["heading"],
                    },
                }
                for c, doc, emb in zip(chunks, documents, embeddings)
            ],
        }

        path = EMBEDDINGS_DIR / f"{item['content_id']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        content_ids.append(item["content_id"])

    context.log.info("Embedded %d items in batch", len(content_ids))
    return content_ids


@dg.op
def gather_embed_ids(results: list[list[str]]) -> list[str]:
    """Flatten batch results. Required wrapper for .collect()."""
    return [cid for batch in results for cid in batch]


# ---------------------------------------------------------------------------
# Graph asset
# ---------------------------------------------------------------------------


@dg.graph_asset(
    group_name="rag_0_baseline",
    description="Compute embeddings for chunked content and write to data/embeddings/",
    ins={"chunks_ready": dg.AssetIn(key="chunked_contents")},
)
def embedded_contents(chunks_ready) -> list[str]:
    items = load_chunked_items(chunks_ready=chunks_ready)
    batches = fan_out_embed_batches(items)
    per_batch = batches.map(embed_batch)
    return gather_embed_ids(per_batch.collect())
