# Dagster assets for knowledge indexing: raw_store.db → ChromaDB.

import logging
import shutil
from datetime import date

import dagster as dg

from knowledge_pipeline.lib.chunking import chunk_markdown
from knowledge_pipeline.lib.config import DATA_DIR, SOURCE_DATA_DIR
from knowledge_pipeline.lib.store import ContentRow, get_contents, set_vector_status

from .resources import RawStoreResource, VectorStoreResource

logger = logging.getLogger(__name__)


@dg.asset(
    group_name="indexing",
    compute_kind="filesystem",
    description="Copy raw_store.db from newsletter-assistant to local data/",
)
def raw_store_copy(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Copy the source database so we never read from the live newsletter-assistant DB."""
    source = SOURCE_DATA_DIR / "raw_store.db"
    if not source.exists():
        raise FileNotFoundError(f"Source database not found: {source}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / "raw_store.db"
    shutil.copy2(source, dest)
    size = dest.stat().st_size

    logger.info("Copied raw_store.db (%d bytes) to %s", size, dest)
    return dg.MaterializeResult(
        metadata={
            "size_bytes": dg.MetadataValue.int(size),
            "source": dg.MetadataValue.path(str(source)),
        }
    )


@dg.asset(
    group_name="indexing",
    compute_kind="sqlite",
    deps=[raw_store_copy],
    description="Fetch content rows from raw_store.db that need vector indexing",
)
def pending_contents(
    context: dg.AssetExecutionContext,
    raw_store: RawStoreResource,
) -> list[ContentRow]:
    """Query for items with pending/ready vector_status."""
    statuses = ["pending", "ready"]
    all_items: list[ContentRow] = []
    db_path = raw_store.get_path()

    for status in statuses:
        items = get_contents(vector_status=status, db_path=db_path)
        all_items.extend(items)

    context.log.info("Found %d content items to index", len(all_items))
    return all_items


@dg.asset(
    group_name="indexing",
    compute_kind="chromadb",
    description="Chunk, embed, and upsert pending content into ChromaDB",
)
def indexed_contents(
    context: dg.AssetExecutionContext,
    pending_contents: list[ContentRow],
    raw_store: RawStoreResource,
    vector_store: VectorStoreResource,
) -> dg.MaterializeResult:
    """For each pending content: chunk → embed → upsert to ChromaDB → update status."""
    if not pending_contents:
        context.log.info("No content items to index.")
        return dg.MaterializeResult(
            metadata={
                "indexed": dg.MetadataValue.int(0),
                "skipped": dg.MetadataValue.int(0),
                "errors": dg.MetadataValue.int(0),
                "total_chunks": dg.MetadataValue.int(0),
            }
        )

    collection = vector_store.get_collection()
    db_path = raw_store.get_path()

    indexed_count = 0
    skipped_count = 0
    error_count = 0
    total_chunks = 0
    details: list[dict] = []

    for item in pending_contents:
        # Skip items with too little content
        if not item.content_md or len(item.content_md.strip()) < 50:
            logger.warning("Skipping %s — content too short", item.content_id)
            set_vector_status(item.content_id, "skip", db_path=db_path)
            skipped_count += 1
            continue

        try:
            chunks = chunk_markdown(item.content_md)
            if not chunks:
                set_vector_status(item.content_id, "skip", db_path=db_path)
                skipped_count += 1
                continue

            metadata: dict = {
                "title": item.title,
                "author": item.author,
                "content_date": item.content_date.isoformat() if item.content_date else "",
            }
            if item.url:
                metadata["url"] = item.url

            # Delete pre-existing chunks for this content_id
            existing = collection.get(where={"content_id": item.content_id})
            if existing["ids"]:
                collection.delete(ids=existing["ids"])

            ids = [f"{item.content_id}::chunk{c.index}" for c in chunks]
            documents = [c.text for c in chunks]
            metadatas = [
                {
                    **metadata,
                    "content_id": item.content_id,
                    "chunk_index": c.index,
                    "heading": c.heading,
                }
                for c in chunks
            ]

            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)  # type: ignore[arg-type]
            set_vector_status(item.content_id, "indexed", db_path=db_path)

            indexed_count += 1
            total_chunks += len(chunks)
            details.append(
                {
                    "content_id": item.content_id,
                    "title": item.title[:60],
                    "source": item.source_key,
                    "chunks": len(chunks),
                }
            )

        except Exception as exc:
            logger.error("Failed to index %s: %s", item.content_id, exc)
            error_count += 1

    # Build a markdown summary for the Dagster UI
    summary_lines = [
        f"**Indexed:** {indexed_count} items ({total_chunks} chunks)",
        f"**Skipped:** {skipped_count} items",
        f"**Errors:** {error_count} items",
    ]
    if details:
        summary_lines.append("\n| content_id | title | source | chunks |")
        summary_lines.append("| --- | --- | --- | --- |")
        for d in details:
            summary_lines.append(
                f"| `{d['content_id']}` | {d['title']} | {d['source']} | {d['chunks']} |"
            )

    return dg.MaterializeResult(
        metadata={
            "indexed": dg.MetadataValue.int(indexed_count),
            "skipped": dg.MetadataValue.int(skipped_count),
            "errors": dg.MetadataValue.int(error_count),
            "total_chunks": dg.MetadataValue.int(total_chunks),
            "summary": dg.MetadataValue.md("\n".join(summary_lines)),
        }
    )
