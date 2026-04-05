# Dagster resources for indexing: SQLite raw store and ChromaDB vector store.

from pathlib import Path

import chromadb
import dagster as dg
from pydantic import PrivateAttr

from knowledge_pipeline.config import CHROMA_PATH, LOCAL_RAW_STORE, SOURCE_RAW_STORE
from knowledge_pipeline.lib.vector_store import get_client, get_collection


class RawStoreResource(dg.ConfigurableResource):
    """Read-only access to raw_store.db (local copy + source for status writes)."""

    db_path: str = str(LOCAL_RAW_STORE)
    source_db_path: str = str(SOURCE_RAW_STORE)

    def get_path(self) -> Path:
        return Path(self.db_path)

    def get_source_path(self) -> Path:
        return Path(self.source_db_path)


class VectorStoreResource(dg.ConfigurableResource):
    """ChromaDB persistent client for embedding storage.

    Set ``embedding_model`` to use a SentenceTransformer model instead of the
    ChromaDB default (all-MiniLM-L6-v2, 256-token limit).  For example,
    ``"nomic-ai/nomic-embed-text-v1.5"`` supports 8192 tokens and scores
    significantly higher on MTEB benchmarks.
    """

    chroma_path: str = str(CHROMA_PATH)
    collection_name: str = "baseline"
    embedding_model: str | None = None

    _client: chromadb.ClientAPI | None = PrivateAttr(default=None)
    _embedding_fn: chromadb.EmbeddingFunction | None = PrivateAttr(default=None)

    def _get_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = get_client(Path(self.chroma_path))
        return self._client

    def _get_embedding_fn(self) -> chromadb.EmbeddingFunction | None:
        if self.embedding_model is None:
            return None  # use lib default (DefaultEmbeddingFunction)
        if self._embedding_fn is None:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            self._embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name=self.embedding_model,
                trust_remote_code=True,
            )
        return self._embedding_fn

    def get_collection(self) -> chromadb.Collection:
        return get_collection(
            client=self._get_client(),
            collection_name=self.collection_name,
            embedding_function=self._get_embedding_fn(),
        )
