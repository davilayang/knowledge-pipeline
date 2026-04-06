# Dagster resources for the markdown + MiniLM index strategy.

from pathlib import Path

import chromadb
import dagster as dg
from pydantic import PrivateAttr

from knowledge_pipeline.config import CHROMA_PATH
from knowledge_pipeline.lib.vector_store import get_client, get_collection


class VectorStoreResource(dg.ConfigurableResource):
    """ChromaDB persistent client for embedding storage.

    Set ``embedding_model`` to use a SentenceTransformer model instead of the
    ChromaDB default (all-MiniLM-L6-v2, 256-token limit).  For example,
    ``"nomic-ai/nomic-embed-text-v1.5"`` supports 8192 tokens and scores
    significantly higher on MTEB benchmarks.
    """

    chroma_path: str = str(CHROMA_PATH)
    collection_name: str = "markdown_minilm"
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
