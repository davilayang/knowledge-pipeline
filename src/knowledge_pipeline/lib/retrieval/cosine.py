# Cosine similarity retrieval wrapping ChromaDB's collection.query().

from __future__ import annotations

import chromadb

from .types import RetrievalResult


class CosineRetrieval:
    """Retrieve chunks via cosine similarity using ChromaDB.

    Wraps ``collection.query()`` and converts cosine distance to a
    similarity score (``score = 1.0 - distance``).
    """

    def __init__(self, collection: chromadb.Collection) -> None:
        self._collection = collection

    @property
    def name(self) -> str:
        return "cosine"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        count = self._collection.count()
        if count == 0:
            return []

        results = self._collection.query(
            query_texts=[query],
            n_results=min(n_results, count),
            include=["documents", "metadatas", "distances"],
        )

        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        dists = results["distances"][0] if results["distances"] else []
        ids = results["ids"][0] if results["ids"] else []

        output: list[RetrievalResult] = []
        for chunk_id, doc, meta, dist in zip(ids, docs, metas, dists):
            output.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    content_id=str(meta.get("content_id", "")),
                    document=doc,
                    score=1.0 - float(dist),
                    metadata=dict(meta),
                )
            )
        return output
