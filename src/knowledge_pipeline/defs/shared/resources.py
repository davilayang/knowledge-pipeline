# src/knowledge_pipeline/defs/shared/resources.py
# Shared Dagster resources used across all index strategies.

from pathlib import Path

import chromadb
import dagster as dg
from pydantic import PrivateAttr

from knowledge_pipeline.config import (
    CHROMA_PATH,
    LOCAL_RAW_STORE,
    SOURCE_RAW_STORE,
    strategy_dir,
)
from knowledge_pipeline.lib.vector_store import get_client, get_collection


# TODO: Check if this is redundant?
class RawStoreResource(dg.ConfigurableResource):
    """Read-only access to raw_store.db (local copy + source for status writes)."""

    db_path: str = str(LOCAL_RAW_STORE)
    source_db_path: str = str(SOURCE_RAW_STORE)

    def get_path(self) -> Path:
        return Path(self.db_path)

    def get_source_path(self) -> Path:
        return Path(self.source_db_path)


class VectorStoreResource(dg.ConfigurableResource):
    """ChromaDB registry — single instance serving all index strategies.

    Call ``get_collection(name, embedding_model)`` to get a specific collection.
    Embedding functions are cached per model name.
    """

    chroma_path: str = str(CHROMA_PATH)

    _client: chromadb.ClientAPI | None = PrivateAttr(default=None)
    _embedding_fns: dict[str, chromadb.EmbeddingFunction] = PrivateAttr(default_factory=dict)

    def _get_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = get_client(Path(self.chroma_path))
        return self._client

    def _get_embedding_fn(self, embedding_model: str) -> chromadb.EmbeddingFunction:
        if embedding_model not in self._embedding_fns:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            self._embedding_fns[embedding_model] = SentenceTransformerEmbeddingFunction(
                model_name=embedding_model,
                trust_remote_code=True,
            )
        return self._embedding_fns[embedding_model]

    def get_collection(
        self,
        collection_name: str,
        embedding_model: str,
    ) -> chromadb.Collection:
        """Get or create a ChromaDB collection with the specified embedding model."""
        return get_collection(
            client=self._get_client(),
            collection_name=collection_name,
            embedding_function=self._get_embedding_fn(embedding_model),
        )


class StrategyPathsResource(dg.ConfigurableResource):
    """Per-strategy data directory helper. Call methods with strategy name."""

    def chunks_dir(self, strategy_name: str) -> Path:
        return strategy_dir(strategy_name, "chunks")

    def embeddings_dir(self, strategy_name: str) -> Path:
        return strategy_dir(strategy_name, "embeddings")
