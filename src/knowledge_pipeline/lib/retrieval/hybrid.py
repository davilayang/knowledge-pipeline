# Hybrid retrieval — combines dense (embedding) and sparse (BM25) retrieval
# using Reciprocal Rank Fusion (RRF) to merge results.

from __future__ import annotations

import chromadb
from rank_bm25 import BM25Okapi

from .types import RetrievalResult


class HybridRetrieval:
    """Combine dense vector search with BM25 sparse retrieval.

    Builds a BM25 index lazily from the ChromaDB collection's documents on
    first ``retrieve()`` call.  Dense and sparse results are merged via
    Reciprocal Rank Fusion (RRF): ``score = sum(1 / (k + rank))`` across
    sources.

    Args:
        collection: ChromaDB collection for both vector search and BM25 corpus.
        candidate_factor: Multiplier for candidates fetched from each source
            before fusion (default 4 — fetches ``4 * n_results`` from each).
        rrf_k: RRF constant (default 60, standard value from the original paper).
    """

    def __init__(
        self,
        collection: chromadb.Collection,
        candidate_factor: int = 4,
        rrf_k: int = 60,
    ) -> None:
        self._collection = collection
        self._candidate_factor = candidate_factor
        self._rrf_k = rrf_k
        # Lazily built on first retrieve()
        self._bm25: BM25Okapi | None = None
        self._corpus_ids: list[str] = []
        self._corpus_docs: list[str] = []
        self._corpus_metas: list[dict] = []

    def _build_bm25_index(self) -> None:
        """Build BM25 index from all documents in the collection."""
        result = self._collection.get(include=["documents", "metadatas"])
        self._corpus_ids = result["ids"]
        self._corpus_docs = result["documents"] or []
        self._corpus_metas = result["metadatas"] or []

        tokenized = [doc.lower().split() for doc in self._corpus_docs]
        self._bm25 = BM25Okapi(tokenized)

    def _get_bm25(self) -> BM25Okapi:
        if self._bm25 is None:
            self._build_bm25_index()
        assert self._bm25 is not None
        return self._bm25

    @property
    def name(self) -> str:
        return "hybrid"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        count = self._collection.count()
        if count == 0:
            return []

        n_candidates = min(n_results * self._candidate_factor, count)

        # --- Dense retrieval (vector) ---
        vector_results = self._collection.query(
            query_texts=[query],
            n_results=n_candidates,
            include=["documents", "metadatas", "distances"],
        )
        vector_ids = vector_results["ids"][0] if vector_results["ids"] else []

        # --- Sparse retrieval (BM25) ---
        bm25 = self._get_bm25()
        tokenized_query = query.lower().split()
        bm25_scores = bm25.get_scores(tokenized_query)

        # Rank all docs by BM25 score, take top n_candidates
        scored_indices = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True,
        )[:n_candidates]
        bm25_ids = [self._corpus_ids[i] for i in scored_indices]

        # --- Reciprocal Rank Fusion ---
        rrf_scores: dict[str, float] = {}
        k = self._rrf_k

        for rank, chunk_id in enumerate(vector_ids, start=1):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

        for rank, chunk_id in enumerate(bm25_ids, start=1):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

        # Sort by fused score
        top_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)[:n_results]

        # --- Build results ---
        # Index vector results for quick lookup
        vec_docs = vector_results["documents"][0] if vector_results["documents"] else []
        vec_metas = vector_results["metadatas"][0] if vector_results["metadatas"] else []
        vector_lookup: dict[str, tuple[str, dict]] = {}
        for cid, doc, meta in zip(vector_ids, vec_docs, vec_metas):
            vector_lookup[cid] = (doc, meta)

        # Index BM25 corpus for quick lookup
        corpus_lookup: dict[str, tuple[str, dict]] = {}
        for cid, doc, meta in zip(self._corpus_ids, self._corpus_docs, self._corpus_metas):
            corpus_lookup[cid] = (doc, meta)

        output: list[RetrievalResult] = []
        for chunk_id in top_ids:
            doc, meta = vector_lookup.get(chunk_id) or corpus_lookup.get(chunk_id, ("", {}))
            output.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    content_id=str(meta.get("content_id", "")),
                    document=doc,
                    score=rrf_scores[chunk_id],
                    metadata=dict(meta),
                )
            )

        return output
