# Shared Dagster resources.
# Notion Queue + queue.db live in `queue_resources.py` (shared by triage and extract).

from pathlib import Path

import chromadb
import dagster as dg
from domains.wiki.state import create_schema
from pydantic import PrivateAttr
from retrievers.vector_store.chroma import get_collection, get_http_client

from orchestrators.config import DATA_DIR, LOCAL_RAW_STORE, SOURCE_RAW_STORE


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


class WikiResource(dg.ConfigurableResource):
    """Paths for the durable wiki store (key "wiki").

    Owned here in `shared` (not a single pipeline) so consumers — sync_wiki_curation
    and the synthesis pipeline — bind one shared instance at the top-level
    Definitions.merge. All durable state (entities / pages / aliases + the
    attributed lane's sources / claims / claim_entities) lives in the SQLite file
    at `wiki_db_path`; get_db_path() ensures the schema is applied (idempotent)
    before any asset touches it. `backup_dir` points at the backup_readings
    snapshot root for the raw-store-per-partition reads the raw synthesis path
    still uses.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    wiki_db_path: str = str(DATA_DIR / "wiki.db")
    backup_dir: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_db_path(self) -> Path:
        """Return the wiki.db path, ensuring the schema exists (idempotent)."""
        path = Path(self.wiki_db_path)
        create_schema(db_path=path)
        return path

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def snapshot_path_for(self, partition_key: str) -> Path:
        """Path to raw_store.db inside the snapshot dir for `partition_key`."""
        return self.get_backup_dir() / partition_key / "raw_store.db"
