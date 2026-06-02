# Shared Dagster resources.
# TODO: Notion Queue page should be moved here and reused by triage and extract DAGs

from pathlib import Path

import chromadb
import dagster as dg
from pydantic import PrivateAttr
from retrievers.vector_store.chroma import get_collection, get_http_client

from orchestrators.config import LOCAL_RAW_STORE, SOURCE_RAW_STORE


class RawStoreResource(dg.ConfigurableResource):
    """Read-only access to raw_store.db (local copy + source for status writes)."""

    db_path: str = str(LOCAL_RAW_STORE)
    source_db_path: str = str(SOURCE_RAW_STORE)

    def get_path(self) -> Path:
        return Path(self.db_path)

    def get_source_path(self) -> Path:
        return Path(self.source_db_path)


class VectorStoreResource(dg.ConfigurableResource):
    """ChromaDB HTTP client — connects to a running Chroma server."""

    chroma_host: str
    chroma_port: int = 8000

    _client: chromadb.ClientAPI | None = PrivateAttr(default=None)

    def _get_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = get_http_client(self.chroma_host, self.chroma_port)
        return self._client

    def get_collection(self, name: str) -> chromadb.Collection:
        """Get or create a ChromaDB collection."""
        return get_collection(self._get_client(), name)
