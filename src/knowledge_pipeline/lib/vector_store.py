# ChromaDB vector store — embed and search content chunks.
#
# Simplified from newsletter-assistant's knowledge.vector_store.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from knowledge_pipeline.lib.config import CHROMA_PATH

COLLECTION_NAME = "contents"


@dataclass
class SearchResult:
    url: str
    title: str
    author: str
    chunk: str
    distance: float


def get_collection(
    collection_name: str = COLLECTION_NAME,
    chroma_path: Path = CHROMA_PATH,
) -> chromadb.Collection:
    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=DefaultEmbeddingFunction(),  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )


def search(
    query: str,
    n_results: int = 5,
    chroma_path: Path = CHROMA_PATH,
) -> list[SearchResult]:
    collection = get_collection(chroma_path=chroma_path)
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    output: list[SearchResult] = []
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    dists = results["distances"][0] if results["distances"] else []

    for doc, meta, dist in zip(docs, metas, dists):
        output.append(
            SearchResult(
                url=str(meta.get("url", "")),
                title=str(meta.get("title", "")),
                author=str(meta.get("author", "")),
                chunk=doc,
                distance=float(dist),
            )
        )
    return output
