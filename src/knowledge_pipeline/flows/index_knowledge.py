# Prefect flow: Copy raw_store.db from newsletter-assistant, then index into ChromaDB.

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import date

from prefect import flow, task
from prefect.artifacts import create_markdown_artifact, create_table_artifact

from knowledge_pipeline.chunking import Chunk, chunk_markdown
from knowledge_pipeline.config import DATA_DIR, LOCAL_RAW_STORE, SOURCE_DATA_DIR
from knowledge_pipeline.store import ContentRow, get_contents, set_vector_status
from knowledge_pipeline.vector_store import get_collection

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    content_id: str
    title: str
    source_key: str
    num_chunks: int
    status: str  # "indexed", "skip", or "error"
    error: str | None = None


# ── Tasks ────────────────────────────────────────────────────────────────────


@task(
    name="copy-raw-store",
    description="Copy raw_store.db from newsletter-assistant to local data/",
    retries=1,
    retry_delay_seconds=5,
)
def copy_raw_store() -> int:
    """Copy raw_store.db from the newsletter-assistant project. Returns file size in bytes."""
    source = SOURCE_DATA_DIR / "raw_store.db"
    if not source.exists():
        raise FileNotFoundError(f"Source database not found: {source}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, LOCAL_RAW_STORE)
    size = LOCAL_RAW_STORE.stat().st_size
    logger.info("Copied raw_store.db (%d bytes) to %s", size, LOCAL_RAW_STORE)
    return size


@task(
    name="fetch-pending-contents",
    description="Query raw_store for contents awaiting vector indexing",
    retries=2,
    retry_delay_seconds=5,
)
def fetch_pending_contents(
    statuses: list[str] | None = None,
    source_key: str | None = None,
    since: date | None = None,
) -> list[ContentRow]:
    if statuses is None:
        statuses = ["pending", "ready"]

    all_items: list[ContentRow] = []
    for status in statuses:
        items = get_contents(vector_status=status, source_key=source_key, since=since)
        all_items.extend(items)

    logger.info("Found %d content items to index (statuses=%s)", len(all_items), statuses)
    return all_items


@task(
    name="chunk-content",
    description="Chunk a single content item using markdown-aware splitting",
)
def chunk_content(item: ContentRow) -> list[Chunk]:
    if not item.content_md or len(item.content_md.strip()) < 50:
        logger.warning(
            "Skipping %s — content too short (%d chars)",
            item.content_id,
            len(item.content_md or ""),
        )
        return []
    chunks = chunk_markdown(item.content_md)
    logger.info("Chunked %s → %d chunks", item.content_id, len(chunks))
    return chunks


@task(
    name="embed-and-upsert",
    description="Embed chunks and upsert into ChromaDB",
    retries=2,
    retry_delay_seconds=10,
)
def embed_and_upsert(item: ContentRow, chunks: list[Chunk]) -> IndexResult:
    if not chunks:
        set_vector_status(item.content_id, "skip")
        return IndexResult(
            content_id=item.content_id,
            title=item.title,
            source_key=item.source_key,
            num_chunks=0,
            status="skip",
        )

    try:
        metadata: dict = {
            "title": item.title,
            "author": item.author,
            "content_date": item.content_date.isoformat() if item.content_date else "",
        }
        if item.url:
            metadata["url"] = item.url

        collection = get_collection()

        # Delete any pre-existing chunks for this content_id
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
        set_vector_status(item.content_id, "indexed")

        return IndexResult(
            content_id=item.content_id,
            title=item.title,
            source_key=item.source_key,
            num_chunks=len(chunks),
            status="indexed",
        )

    except Exception as exc:
        logger.error("Failed to index %s: %s", item.content_id, exc)
        return IndexResult(
            content_id=item.content_id,
            title=item.title,
            source_key=item.source_key,
            num_chunks=0,
            status="error",
            error=str(exc),
        )


@task(
    name="create-index-report",
    description="Create a Prefect artifact summarising the indexing run",
)
def create_index_report(results: list[IndexResult]) -> dict:
    indexed = [r for r in results if r.status == "indexed"]
    skipped = [r for r in results if r.status == "skip"]
    errors = [r for r in results if r.status == "error"]
    total_chunks = sum(r.num_chunks for r in indexed)

    if indexed:
        table_data = [
            {
                "content_id": r.content_id,
                "title": r.title[:60],
                "source": r.source_key,
                "chunks": r.num_chunks,
            }
            for r in indexed
        ]
        create_table_artifact(
            key="index-knowledge-details",
            table=table_data,
            description="Content items indexed in this run",
        )

    summary_lines = [
        f"**Indexed:** {len(indexed)} items ({total_chunks} chunks)",
        f"**Skipped:** {len(skipped)} items (content too short)",
        f"**Errors:** {len(errors)} items",
    ]
    if errors:
        summary_lines.append("\n### Errors")
        for r in errors:
            summary_lines.append(f"- `{r.content_id}`: {r.error}")

    create_markdown_artifact(
        key="index-knowledge-summary",
        markdown="\n".join(summary_lines),
        description="Knowledge indexing run summary",
    )

    summary = {
        "indexed": len(indexed),
        "skipped": len(skipped),
        "errors": len(errors),
        "total_chunks": total_chunks,
    }
    logger.info("Index report: %s", summary)
    return summary


# ── Flow ─────────────────────────────────────────────────────────────────────


@flow(
    name="index-knowledge",
    description=(
        "Copy raw_store.db from newsletter-assistant, then index pending content "
        "into the local ChromaDB knowledge store using markdown-aware chunking."
    ),
    retries=0,
)
def index_knowledge(
    source_key: str | None = None,
    since: date | None = None,
    statuses: list[str] | None = None,
    skip_copy: bool = False,
) -> dict:
    """Main flow: copy db → fetch pending → chunk → embed → upsert → report.

    Args:
        source_key: Filter by newsletter source (e.g. "medium", "the_batch").
        since: Only index content from this date onward.
        statuses: Vector statuses to process (default: ["pending", "ready"]).
        skip_copy: If True, skip copying raw_store.db (use existing local copy).
    """
    if not skip_copy:
        copy_raw_store()

    items = fetch_pending_contents(statuses=statuses, source_key=source_key, since=since)

    if not items:
        logger.info("No content items to index.")
        create_markdown_artifact(
            key="index-knowledge-summary",
            markdown="**No content items to index.** All items are already indexed or skipped.",
            description="Knowledge indexing run summary",
        )
        return {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}

    results: list[IndexResult] = []
    for item in items:
        chunks = chunk_content(item)
        result = embed_and_upsert(item, chunks)
        results.append(result)

    return create_index_report(results)
