# Dagster resources for indexing: SQLite raw store and ChromaDB vector store.

from pathlib import Path

import chromadb
import dagster as dg
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from knowledge_pipeline.lib.config import CHROMA_PATH, LOCAL_RAW_STORE


class RawStoreResource(dg.ConfigurableResource):
    """Read-only access to the local copy of raw_store.db."""

    db_path: str = str(LOCAL_RAW_STORE)

    def get_path(self) -> Path:
        return Path(self.db_path)


class VectorStoreResource(dg.ConfigurableResource):
    """ChromaDB persistent client for embedding storage."""

    chroma_path: str = str(CHROMA_PATH)
    collection_name: str = "contents"

    def get_collection(self) -> chromadb.Collection:
        path = Path(self.chroma_path)
        path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(path))
        return client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=DefaultEmbeddingFunction(),  # type: ignore[arg-type]
            metadata={"hnsw:space": "cosine"},
        )
