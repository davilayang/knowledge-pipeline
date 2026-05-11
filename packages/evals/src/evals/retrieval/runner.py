"""Eval runner — index items, run queries, compute per-source metrics.

The runner is decoupled from concrete dependencies so unit tests can pass
in-memory chroma + a deterministic fake embedder + a list of pre-built
``IngestItem``s without ever touching the network.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import chromadb
from domains.types import IngestItem
from retrievers.chunking.registry import get_chunking_fn
from retrievers.chunking.types import Chunk

from .dataset import group_by_source
from .embedder import Embedder
from .metrics import aggregate_mean, aggregate_recall, hit_at_k, mrr_at_k, ndcg_at_k
from .types import EvalConfig, EvalPair, EvalRunResult, SourceMetrics

ChunkingFn = Callable[[str], list[Chunk]]


@dataclass
class _IndexedSource:
    source: str
    collection: chromadb.Collection
    n_items: int = 0
    n_chunks: int = 0


def run_eval(
    *,
    config: EvalConfig,
    eval_pairs: list[EvalPair],
    items_by_source: dict[str, list[IngestItem]],
    embedder: Embedder,
    chroma_client: chromadb.ClientAPI,
    collection_prefix: str | None = None,
) -> EvalRunResult:
    """Drive a full eval: index items per source, run queries, aggregate metrics.

    The function does not delete its collections — callers (the CLI) are
    responsible for that so test code can assert on collection state and
    long-running experiments can keep them around for inspection.
    """
    started = datetime.now(tz=UTC)
    prefix = collection_prefix or f"eval_{started.strftime('%Y%m%d_%H%M%S')}"

    indexed: dict[str, _IndexedSource] = {}
    for source, items in items_by_source.items():
        chunker_name = config.chunker_by_source.get(source)
        if chunker_name is None:
            continue
        capped = items if config.item_limit is None else items[: config.item_limit]
        indexed[source] = _index_source(
            source=source,
            items=capped,
            chunker=get_chunking_fn(
                chunker_name,
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
            ),
            embedder=embedder,
            chroma_client=chroma_client,
            collection_name=f"{prefix}_{source}",
        )

    pairs_by_source = group_by_source(eval_pairs)
    per_source: list[SourceMetrics] = []
    for source, pairs in pairs_by_source.items():
        if not pairs or source not in indexed:
            continue
        per_source.append(
            _score_source(
                source=source,
                pairs=pairs,
                indexed=indexed[source],
                embedder=embedder,
                recall_k=config.recall_k,
                rank_k=config.rank_k,
            )
        )

    return EvalRunResult(
        embedding_model=config.embedding_model,
        embedding_dims=config.embedding_dims,
        chunker_by_source=dict(config.chunker_by_source),
        per_source=per_source,
        started_at=started.isoformat(),
        finished_at=datetime.now(tz=UTC).isoformat(),
    )


def _index_source(
    *,
    source: str,
    items: list[IngestItem],
    chunker: ChunkingFn,
    embedder: Embedder,
    chroma_client: chromadb.ClientAPI,
    collection_name: str,
) -> _IndexedSource:
    # ``embedding_function=None`` — we ship pre-computed vectors; chroma must
    # not silently default-embed (which would mix tokenisers).
    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=None,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    for item in items:
        chunks = chunker(item.text)
        for c in chunks:
            ids.append(_chunk_id(item.item_id, c.index))
            docs.append(c.text)
            metas.append(
                {
                    "content_id": item.item_id,
                    "chunk_index": c.index,
                    "source_type": item.source_type,
                    "_embedding_model": embedder.model,
                    "_embedding_dims": embedder.dims,
                }
            )

    if ids:
        embeddings = embedder.embed_batch(docs)
        # Chroma rejects batches above its server-side max (default 5461). Stay
        # well under the cap so headroom for future server-side changes.
        BATCH = 4000
        for start in range(0, len(ids), BATCH):
            stop = start + BATCH
            collection.upsert(
                ids=ids[start:stop],
                documents=docs[start:stop],
                embeddings=embeddings[start:stop],
                metadatas=metas[start:stop],
            )

    return _IndexedSource(
        source=source,
        collection=collection,
        n_items=len(items),
        n_chunks=len(ids),
    )


def _score_source(
    *,
    source: str,
    pairs: list[EvalPair],
    indexed: _IndexedSource,
    embedder: Embedder,
    recall_k: int,
    rank_k: int,
) -> SourceMetrics:
    if indexed.n_chunks == 0:
        return SourceMetrics(
            source=source,
            n_queries=len(pairs),
            recall_at_5=0.0,
            mrr_at_10=0.0,
            ndcg_at_10=0.0,
        )
    query_vecs = embedder.embed_batch([p.query for p in pairs])
    n_results = min(rank_k, indexed.n_chunks)
    response = indexed.collection.query(
        query_embeddings=query_vecs,
        n_results=n_results,
        include=["metadatas"],
    )
    metadatas = response.get("metadatas") or []

    hits: list[int] = []
    rrs: list[float] = []
    ndcgs: list[float] = []
    for pair, per_query_metas in zip(pairs, metadatas):
        retrieved = [str(m.get("content_id", "")) for m in (per_query_metas or [])]
        hits.append(hit_at_k(retrieved, pair.expected_content_id, recall_k))
        rrs.append(mrr_at_k(retrieved, pair.expected_content_id, rank_k))
        ndcgs.append(ndcg_at_k(retrieved, pair.expected_content_id, rank_k))

    return SourceMetrics(
        source=source,
        n_queries=len(pairs),
        recall_at_5=aggregate_recall(hits),
        mrr_at_10=aggregate_mean(rrs),
        ndcg_at_10=aggregate_mean(ndcgs),
    )


def _chunk_id(content_id: str, index: int) -> str:
    return f"{content_id}::chunk{index}"
