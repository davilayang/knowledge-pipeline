# Op factories for index pipelines.
# Each factory creates ops bound to a specific strategy's config
# (strategy_name, collection_name, embedding_model).

import json
import logging

import dagster as dg

from knowledge_pipeline.lib.store import get_contents

from .resources import RawStoreResource, StrategyPathsResource, VectorStoreResource

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


def _safe_filename(content_id: str) -> str:
    """Sanitize content_id for use as a filename. Truncate + hash if too long."""
    import hashlib
    import re

    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", content_id)
    if len(safe) > 200:
        h = hashlib.sha256(content_id.encode()).hexdigest()[:12]
        safe = safe[:180] + f"__{h}"
    return safe


# ---------------------------------------------------------------------------
# Chunking op factories
# ---------------------------------------------------------------------------


def create_chunk_batch_op(
    strategy_name: str,
    chunking: str = "markdown",
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> dg.OpDefinition:
    """Create a chunk_batch op that writes to the given strategy's chunks dir."""
    from knowledge_pipeline.lib.chunking import get_chunking_fn

    chunking_fn = get_chunking_fn(chunking, chunk_size, chunk_overlap)

    @dg.op(name=f"chunk_batch_{strategy_name}")
    def _chunk_batch(
        context: dg.OpExecutionContext,
        batch: list[dict],
        strategy_paths: StrategyPathsResource,
    ) -> list[str]:
        chunks_dir = strategy_paths.chunks_dir(strategy_name)
        chunks_dir.mkdir(parents=True, exist_ok=True)
        content_ids = []
        for item in batch:
            try:
                chunks = chunking_fn(item["content_md"])
            except Exception as exc:
                logger.error("Failed to chunk %s: %s", item["content_id"], exc)
                continue

            if not chunks:
                continue

            record = {
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
            path = chunks_dir / f"{_safe_filename(item['content_id'])}.json"
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            content_ids.append(item["content_id"])

        context.log.info("Chunked %d items in batch, wrote to %s", len(content_ids), chunks_dir)
        return content_ids

    return _chunk_batch


# ---------------------------------------------------------------------------
# Embedding op factories
# ---------------------------------------------------------------------------


def create_load_chunked_items_op(strategy_name: str) -> dg.OpDefinition:
    """Create a load op that reads from the given strategy's chunks dir."""

    @dg.op(
        name=f"load_chunked_items_{strategy_name}",
        ins={"chunks_ready": dg.In(dagster_type=dg.Nothing)},
    )
    def _load(
        context: dg.OpExecutionContext,
        strategy_paths: StrategyPathsResource,
    ) -> list[dict]:
        chunks_dir = strategy_paths.chunks_dir(strategy_name)
        if not chunks_dir.exists():
            context.log.warning("Chunks directory not found: %s", chunks_dir)
            return []
        items = []
        for path in sorted(chunks_dir.glob("*.json")):
            items.append(json.loads(path.read_text(encoding="utf-8")))
        context.log.info("Loaded %d chunked items from %s", len(items), chunks_dir)
        return items

    return _load


def create_embed_batch_op(
    strategy_name: str,
    collection_name: str,
    embedding_model: str,
) -> dg.OpDefinition:
    """Create an embed op bound to a specific collection and embedding model."""

    @dg.op(name=f"embed_batch_{strategy_name}")
    def _embed(
        context: dg.OpExecutionContext,
        batch: list[dict],
        vector_store: VectorStoreResource,
        strategy_paths: StrategyPathsResource,
    ) -> list[str]:
        embeddings_dir = strategy_paths.embeddings_dir(strategy_name)
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        collection = vector_store.get_collection(collection_name, embedding_model)
        ef = collection._embedding_function  # noqa: SLF001
        content_ids = []

        for item in batch:
            chunks = item["chunks"]
            documents = [
                f"Title: {item['title']} | Author: {item['author']}\n{c['text']}" for c in chunks
            ]
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

            path = embeddings_dir / f"{_safe_filename(item['content_id'])}.json"
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            content_ids.append(item["content_id"])

        context.log.info("Embedded %d items in batch", len(content_ids))
        return content_ids

    return _embed


# ---------------------------------------------------------------------------
# Indexing op factory
# ---------------------------------------------------------------------------


def create_indexing_asset(
    strategy_name: str,
    collection_name: str,
    embedding_model: str,
    group_name: str,
    deps: list[str],
    asset_name: str | None = None,
) -> dg.AssetsDefinition:
    """Create an indexing asset bound to a specific strategy."""

    @dg.asset(
        name=asset_name or f"{strategy_name}_indexed",
        group_name=group_name,
        compute_kind="chromadb",
        deps=deps,
        description=f"Upsert embeddings to ChromaDB collection '{collection_name}'",
    )
    def _indexed(
        context: dg.AssetExecutionContext,
        vector_store: VectorStoreResource,
        strategy_paths: StrategyPathsResource,
    ) -> dg.MaterializeResult:
        embeddings_dir = strategy_paths.embeddings_dir(strategy_name)
        if not embeddings_dir.exists():
            context.log.warning("Embeddings directory not found: %s", embeddings_dir)
            return dg.MaterializeResult(metadata={"indexed": dg.MetadataValue.int(0)})

        collection = vector_store.get_collection(collection_name, embedding_model)

        indexed_count = 0
        error_count = 0
        total_chunks = 0
        details: list[dict] = []

        for path in sorted(embeddings_dir.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            content_id = record["content_id"]

            try:
                chunks = record["chunks"]

                existing = collection.get(where={"content_id": content_id})
                if existing["ids"]:
                    collection.delete(ids=existing["ids"])

                ids = [c["id"] for c in chunks]
                documents = [c["document"] for c in chunks]
                embeddings_data = [c["embedding"] for c in chunks]
                metadatas = [c["metadata"] for c in chunks]

                collection.upsert(
                    ids=ids,
                    documents=documents,
                    embeddings=embeddings_data,  # type: ignore[arg-type]
                    metadatas=metadatas,  # type: ignore[arg-type]
                )
                indexed_count += 1
                total_chunks += len(chunks)
                details.append(
                    {
                        "content_id": content_id,
                        "title": record.get("metadata_base", {}).get("title", "")[:60],
                        "source": record.get("source_key", ""),
                        "chunks": len(chunks),
                    }
                )
            except Exception as exc:
                logger.error("Failed to index %s: %s", content_id, exc)
                error_count += 1

        summary_lines = [
            f"**Indexed:** {indexed_count} items ({total_chunks} chunks)",
            f"**Errors:** {error_count} items",
        ]
        if details:
            summary_lines.append("\n| content_id | title | source | chunks |")
            summary_lines.append("| --- | --- | --- | --- |")
            for d in details:
                summary_lines.append(
                    f"| `{d['content_id']}` | {d['title']} | {d['source']} | {d['chunks']} |"
                )

        context.log.info("Indexed %d items, %d errors", indexed_count, error_count)
        return dg.MaterializeResult(
            metadata={
                "indexed": dg.MetadataValue.int(indexed_count),
                "errors": dg.MetadataValue.int(error_count),
                "total_chunks": dg.MetadataValue.int(total_chunks),
                "summary": dg.MetadataValue.md("\n".join(summary_lines)),
            }
        )

    return _indexed


# ---------------------------------------------------------------------------
# Shared ops (not strategy-specific)
# ---------------------------------------------------------------------------


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


@dg.op(out=dg.DynamicOut())
def fan_out_batches(context: dg.OpExecutionContext, items: list[dict]):
    """Split items into batches and yield as DynamicOutputs."""
    if not items:
        context.log.info("No items to process.")
        return
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i : i + BATCH_SIZE]
        yield dg.DynamicOutput(batch, mapping_key=f"batch_{i // BATCH_SIZE}")


@dg.op
def gather_ids(results: list[list[str]]) -> list[str]:
    """Flatten batch results into a list of content_ids."""
    return [cid for batch in results for cid in batch]
