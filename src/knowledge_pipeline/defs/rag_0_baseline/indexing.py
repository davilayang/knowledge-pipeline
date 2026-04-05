# Asset: indexed_contents — read pre-computed embeddings, upsert to ChromaDB,
# update vector_status in source DB.

import json
import logging

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.config import EMBEDDINGS_DIR
from knowledge_pipeline.lib.store import set_vector_status

from .resources import RawStoreResource, VectorStoreResource

logger = logging.getLogger(__name__)


@dg.asset(
    group_name="rag_0_baseline",
    compute_kind="chromadb",
    deps=["embedded_contents"],
    description="Upsert pre-computed embeddings to ChromaDB and update vector_status",
)
def indexed_contents(
    context: AssetExecutionContext,
    raw_store: RawStoreResource,
    vector_store: VectorStoreResource,
) -> dg.MaterializeResult:
    """Read embedding JSONs, upsert to ChromaDB, finalize status."""
    if not EMBEDDINGS_DIR.exists():
        context.log.warning("Embeddings directory not found: %s", EMBEDDINGS_DIR)
        return dg.MaterializeResult(metadata={"indexed": dg.MetadataValue.int(0)})

    collection = vector_store.get_collection()
    db_path = raw_store.get_path()

    indexed_count = 0
    error_count = 0
    total_chunks = 0
    details: list[dict] = []

    for path in sorted(EMBEDDINGS_DIR.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        content_id = record["content_id"]

        try:
            chunks = record["chunks"]

            # Delete pre-existing chunks for this content_id
            existing = collection.get(where={"content_id": content_id})
            if existing["ids"]:
                collection.delete(ids=existing["ids"])

            ids = [c["id"] for c in chunks]
            documents = [c["document"] for c in chunks]
            embeddings = [c["embedding"] for c in chunks]
            metadatas = [c["metadata"] for c in chunks]

            collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,  # type: ignore[arg-type]
                metadatas=metadatas,  # type: ignore[arg-type]
            )
            set_vector_status(content_id, "indexed", db_path=db_path)
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
            set_vector_status(content_id, "error", db_path=db_path)
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
