# Resources for the synthesize_wiki pipeline.
#
# Notion "Wiki Pages" I/O moved OUT entirely (B2): synthesis reads the local
# rejected_entities table, not Notion, and all Notion Wiki Pages access now
# lives in the sync_wiki_curation curation DAG. This pipeline owns only the
# durable wiki store (WikiResource, key "wiki"); sync_wiki_curation binds the
# same "wiki" resource at the top-level Definitions.merge (it does not
# re-declare it — that would collide on the resource key).

from pathlib import Path

import dagster as dg
from domains.wiki.state import create_schema

from orchestrators.config import DATA_DIR


class WikiResource(dg.ConfigurableResource):
    """Paths for wiki synthesis.

    Reads the source raw_store from the backup_readings snapshot for the
    current partition (`backup_dir/<partition_key>/raw_store.db`). 1:1
    binding via IdentityPartitionMapping — if the snapshot is missing,
    wiki/pending raises rather than falling back to anything older.

    All durable state (the processed / pages / aliases / page_sources tables)
    lives in the SQLite file at `wiki_db_path`, alongside the other local
    stores under the host-mounted data dir (survives `down -v`). get_db_path()
    ensures the schema is applied (idempotent) before any asset touches it.
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
        """Path to raw_store.db inside the snapshot dir for `partition_key`.

        Pure derivation; does not check that the file exists. Callers
        (wiki/pending) handle the missing-file case."""
        return self.get_backup_dir() / partition_key / "raw_store.db"


def build_resources() -> dict[str, dg.ConfigurableResource]:
    # No Notion resource here: synthesis reads the denylist from the local
    # rejected_entities table, so nothing in this pipeline consumes Notion.
    # WikiPagesNotionResource moved to the sync_wiki_curation DAG, which owns all
    # Notion "Wiki Pages" I/O (keeping the NOTION_* env vars off this pipeline).
    return {
        "wiki": WikiResource(
            backup_dir=dg.EnvVar("BACKUP_DST_DIR"),
        ),
    }
