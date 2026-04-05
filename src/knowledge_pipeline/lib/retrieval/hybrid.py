# Hybrid retrieval — combines dense (embedding) and sparse (BM25) retrieval.
# Stub: not yet implemented (requires BM25 corpus setup).

from __future__ import annotations

from typing import Any

import chromadb

from .types import RetrievalResult


class HybridRetrieval:
    """Combine dense vector search with BM25 sparse retrieval.

    Uses reciprocal rank fusion to merge results from both sources.
    """

    def __init__(self, collection: chromadb.Collection, bm25_corpus: Any = None) -> None:
        self._collection = collection
        self._bm25_corpus = bm25_corpus

    @property
    def name(self) -> str:
        return "hybrid"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        raise NotImplementedError("HybridRetrieval is a stub — add BM25 dependency to implement.")
