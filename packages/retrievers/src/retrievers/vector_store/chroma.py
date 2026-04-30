# ChromaDB vector store — embed and search content chunks.

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

COLLECTION_NAME = "contents"
# DefaultEmbeddingFunction uses all-MiniLM-L6-v2 via onnxruntime.
# Pinned here so all code paths use the same function and model.
EMBEDDING_FUNCTION = DefaultEmbeddingFunction()


@dataclass
class SearchResult:
    url: str
    title: str
    author: str
    chunk: str
    distance: float


def get_client(path: Path) -> chromadb.ClientAPI:
    """Create a ChromaDB persistent client.

    Args:
        path: Directory path for ChromaDB persistence storage.
              The caller (orchestrators layer) is responsible for supplying
              the correct path — this module does not import config.
    """
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def get_collection(
    client: chromadb.ClientAPI | None = None,
    collection_name: str = COLLECTION_NAME,
    chroma_path: Path | None = None,
    embedding_function: chromadb.EmbeddingFunction | None = None,
) -> chromadb.Collection:
    """Get or create a ChromaDB collection. Optionally reuse an existing client.

    Args:
        client: Existing ChromaDB client to reuse. If None, creates one from chroma_path.
        collection_name: Name of the collection to get or create.
        chroma_path: Path for ChromaDB persistence (required if client is None).
        embedding_function: Embedding function to use; defaults to EMBEDDING_FUNCTION.
    """
    if client is None:
        if chroma_path is None:
            raise ValueError("chroma_path is required when client is not provided")
        client = get_client(chroma_path)
    ef = embedding_function or EMBEDDING_FUNCTION
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=ef,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )


def search(
    query: str,
    chroma_path: Path,
    n_results: int = 5,
    collection_name: str = COLLECTION_NAME,
) -> list[SearchResult]:
    """Search a ChromaDB collection for relevant chunks.

    Args:
        query: Search query string.
        chroma_path: Path to ChromaDB persistence directory.
        n_results: Maximum number of results to return.
        collection_name: Name of the collection to search.
    """
    collection = get_collection(collection_name=collection_name, chroma_path=chroma_path)
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
