# Assets for populate_vector_store. See README.md for the DAG diagram and runbook.

from itertools import islice

import dagster as dg
from domains.types import IngestItem
from retrievers.chunking.registry import get_chunking_fn
from retrievers.chunking.types import Chunk
from retrievers.embedding import OpenAIEmbedder

from orchestrators.config import POPULATE_VECTOR_STORE_DAG_VERSION
from orchestrators.defs.shared.resources import VectorStoreResource

from .def_config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHUNKER_BY_SOURCE,
    COLLECTION_CONTENTS,
    COLLECTION_CONVERSATIONS,
    COLLECTION_NOTES,
    EMBEDDING_DIMS_DEFAULT,
    EMBEDDING_MODEL_DEFAULT,
    MAX_PER_TICK_DEFAULT,
    PIPELINE_TAG,
    vector_store_partition_def,
)
from .resources import SourcesResource

_CHROMA_IN_BATCH = 500
_UPSERT_BATCH = 4000


SOURCE_TO_COLLECTION: list[tuple[str, str]] = [
    ("raw_store", COLLECTION_CONTENTS),
    ("notes", COLLECTION_NOTES),
    ("sessions", COLLECTION_CONVERSATIONS),
]


def _chunked(seq, n: int):
    it = iter(seq)
    while batch := list(islice(it, n)):
        yield batch


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _metadata_common(
    item: IngestItem, chunk: Chunk, chunk_index: int, model: str, dims: int
) -> dict:
    md: dict = {
        "content_id": item.item_id,
        "chunk_index": chunk_index,
        "_embedding_model": model,
        "_embedding_dims": dims,
    }
    if chunk.heading:
        md["heading_path"] = chunk.heading
    if item.title:
        md["title"] = item.title
    if item.author:
        md["author"] = item.author
    if item.date:
        md["content_date"] = item.date.isoformat()
    if item.url:
        md["url"] = item.url
    if item.started_at:
        md["started_at"] = item.started_at.isoformat()
    if item.source_ref:
        md["source_ref"] = item.source_ref
    return md


def _process_item(
    item: IngestItem,
    chunker,
    embedder: OpenAIEmbedder,
    collection,
    model: str,
    dims: int,
    heading_in_embed: bool,
) -> tuple[int, int]:
    """Chunk + embed + delete-then-upsert a single item.

    Returns ``(chunks_written, tokens_embedded_estimate)``.

    When ``heading_in_embed`` is True, the chunk's heading breadcrumb is
    prepended to the embedded text (not the stored ``document`` field) so the
    vector encodes section context. The stored Chroma ``documents`` stay
    clean for downstream consumers.
    """
    chunks: list[Chunk] = chunker(item.text or "")
    if not chunks:
        collection.delete(where={"content_id": item.item_id})
        return (0, 0)

    embed_texts = [
        f"{c.heading}\n\n{c.text}" if (heading_in_embed and c.heading) else c.text for c in chunks
    ]
    store_texts = [c.text for c in chunks]
    embeddings = embedder.embed_batch(embed_texts)
    ids = [f"{item.item_id}::chunk-{i}" for i in range(len(chunks))]
    metadatas = [_metadata_common(item, c, i, model, dims) for i, c in enumerate(chunks)]

    collection.delete(where={"content_id": item.item_id})
    for sl in _chunked(range(len(chunks)), _UPSERT_BATCH):
        lo, hi = sl[0], sl[-1] + 1
        collection.upsert(
            ids=ids[lo:hi],
            documents=store_texts[lo:hi],
            embeddings=embeddings[lo:hi],
            metadatas=metadatas[lo:hi],
        )
    tokens = sum(_estimate_tokens(t) for t in embed_texts)
    return (len(chunks), tokens)


@dg.asset(
    key=["vector_store", "pending"],
    group_name="vector_store",
    partitions_def=vector_store_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    code_version=POPULATE_VECTOR_STORE_DAG_VERSION,
    kinds={"python"},
)
def pending(
    context: dg.AssetExecutionContext,
    sources: SourcesResource,
    vector_store: VectorStoreResource,
) -> dg.Output[dict[str, list[str]]]:
    out: dict[str, list[str]] = {}
    totals: dict[str, int] = {}
    for name, collection_name in SOURCE_TO_COLLECTION:
        source = getattr(sources, name)()
        all_ids = source.get_item_ids()
        collection = vector_store.get_collection(collection_name)
        existing: set[str] = set()
        for batch in _chunked(all_ids, _CHROMA_IN_BATCH):
            rows = collection.get(where={"content_id": {"$in": list(batch)}}, include=[])
            for cid in rows.get("ids") or []:
                existing.add(cid.split("::chunk-")[0])
        items = [i for i in all_ids if i not in existing][:MAX_PER_TICK_DEFAULT]
        out[name] = items
        totals[name] = len(all_ids)
        context.log.info(
            "discovery %s: %d pending (total seen=%d, already indexed=%d)",
            name,
            len(items),
            len(all_ids),
            len(existing),
        )
    summary = " · ".join(f"{n}={len(out[n])}/{totals[n]}" for n, _ in SOURCE_TO_COLLECTION)
    return dg.Output(
        out,
        metadata={
            "summary": dg.MetadataValue.md(f"**pending** — {summary}"),
            "pending_by_source": dg.MetadataValue.json({k: len(v) for k, v in out.items()}),
            "total_by_source": dg.MetadataValue.json(totals),
        },
    )


def _run_ingest(
    context: dg.AssetExecutionContext,
    pending: dict[str, list[str]],
    sources: SourcesResource,
    vector_store: VectorStoreResource,
    source_name: str,
    collection_name: str,
) -> dg.MaterializeResult:
    item_ids = pending.get(source_name, [])
    if not item_ids:
        return dg.MaterializeResult(metadata={"summary": dg.MetadataValue.md("_no pending_")})

    source = getattr(sources, source_name)()
    chunker_name = CHUNKER_BY_SOURCE[source_name]
    chunker = get_chunking_fn(chunker_name, CHUNK_SIZE, CHUNK_OVERLAP)
    # Prepend the heading breadcrumb to embedded text for markdown chunkers
    # (semantic section path); skip for turn_grouping where the heading is a
    # time-range that would pollute the vector.
    heading_in_embed = chunker_name == "markdown"
    model = EMBEDDING_MODEL_DEFAULT
    dims = EMBEDDING_DIMS_DEFAULT
    collection = vector_store.get_collection(collection_name)
    embedder = OpenAIEmbedder(model=model, dims=dims)

    items: list[IngestItem] = []
    missing: list[str] = []
    for iid in item_ids:
        item = source.get_item(iid)
        if item is None:
            missing.append(iid)
        else:
            items.append(item)

    errors: list[tuple[str, str]] = []
    chunks_written = 0
    tokens_embedded = 0
    # Throughput-optimisation options if per-tick latency becomes a constraint:
    #   - Cross-item embedding batch — collapse N OpenAIEmbedder.embed_batch
    #     calls into one (sub-batched at 250k tokens internally). 10-50x
    #     fewer OpenAI requests; chunking + delete-then-upsert stay per-item
    #     for the idempotent contract. See README "Future optimizations".
    #   - ThreadPoolExecutor inside this loop — ~4x for I/O-bound embed
    #     calls; plain Python, not Dagster-native. Both this repo and
    #     dagster-open-platform avoid this idiom.
    #   - Graph-backed asset with DynamicOutput — real Dagster fan-out
    #     across ops, multiprocess executor parallelises; ~100 LOC plus
    #     ~1-2s/op orchestration overhead per item.
    for i, item in enumerate(items, 1):
        context.log.info("[%d/%d] ingesting %s", i, len(items), item.item_id)
        try:
            cw, tk = _process_item(
                item, chunker, embedder, collection, model, dims, heading_in_embed
            )
        except Exception as e:
            context.log.exception("ingest failed for %s", item.item_id)
            errors.append((item.item_id, repr(e)))
        else:
            chunks_written += cw
            tokens_embedded += tk

    metadata: dict[str, dg.MetadataValue] = {
        "summary": dg.MetadataValue.md(
            f"**{len(items) - len(errors)}/{len(items)} items** — "
            f"{chunks_written} chunks, ~{tokens_embedded} tokens"
            + (f" ({len(missing)} missing from source)" if missing else "")
        ),
        "items_ingested": dg.MetadataValue.int(len(items) - len(errors)),
        "chunks_written": dg.MetadataValue.int(chunks_written),
        "tokens_embedded": dg.MetadataValue.int(tokens_embedded),
        "errors": dg.MetadataValue.json(errors),
        "missing": dg.MetadataValue.json(missing),
        "embedding_model": dg.MetadataValue.text(model),
        "embedding_dims": dg.MetadataValue.int(dims),
    }
    if errors:
        raise dg.Failure(
            description=f"{len(errors)} item(s) raised during ingest",
            metadata=metadata,
        )
    return dg.MaterializeResult(metadata=metadata)


@dg.asset(
    key=["vector_store", "contents"],
    group_name="vector_store",
    partitions_def=vector_store_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    code_version=POPULATE_VECTOR_STORE_DAG_VERSION,
    kinds={"chromadb", "openai"},
    ins={"pending": dg.AssetIn(["vector_store", "pending"])},
)
def contents(
    context: dg.AssetExecutionContext,
    pending: dict[str, list[str]],
    sources: SourcesResource,
    vector_store: VectorStoreResource,
) -> dg.MaterializeResult:
    return _run_ingest(context, pending, sources, vector_store, "raw_store", COLLECTION_CONTENTS)


@dg.asset(
    key=["vector_store", "conversations"],
    group_name="vector_store",
    partitions_def=vector_store_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    code_version=POPULATE_VECTOR_STORE_DAG_VERSION,
    kinds={"chromadb", "openai"},
    ins={"pending": dg.AssetIn(["vector_store", "pending"])},
)
def conversations(
    context: dg.AssetExecutionContext,
    pending: dict[str, list[str]],
    sources: SourcesResource,
    vector_store: VectorStoreResource,
) -> dg.MaterializeResult:
    return _run_ingest(
        context, pending, sources, vector_store, "sessions", COLLECTION_CONVERSATIONS
    )


@dg.asset(
    key=["vector_store", "notes"],
    group_name="vector_store",
    partitions_def=vector_store_partition_def,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    code_version=POPULATE_VECTOR_STORE_DAG_VERSION,
    kinds={"chromadb", "openai"},
    ins={"pending": dg.AssetIn(["vector_store", "pending"])},
)
def notes(
    context: dg.AssetExecutionContext,
    pending: dict[str, list[str]],
    sources: SourcesResource,
    vector_store: VectorStoreResource,
) -> dg.MaterializeResult:
    return _run_ingest(context, pending, sources, vector_store, "notes", COLLECTION_NOTES)


all_assets = [pending, contents, conversations, notes]
