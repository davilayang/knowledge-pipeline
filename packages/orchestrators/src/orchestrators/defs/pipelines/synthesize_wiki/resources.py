# Resources for the synthesize_wiki pipeline.

from pathlib import Path

import dagster as dg

from orchestrators.config import BACKUP_DIR, DATA_DIR


class WikiResource(dg.ConfigurableResource):
    """Paths for wiki synthesis.

    Reads the source raw_store from the backup_readings snapshot for the
    current partition (`backup_dir/<partition_key>/raw_store.db`). 1:1
    binding via IdentityPartitionMapping — if the snapshot is missing,
    wiki/pending raises rather than falling back to anything older.

    All durable state (wiki.processed / wiki.pages / wiki.aliases plus the
    LangGraph checkpoints) lives in the Postgres pointed to by
    `database_url`. Resolved at run init via dg.EnvVar — an unset
    DATABASE_URL fails fast rather than leaking a cryptic psycopg error
    from inside the workflow.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    backup_dir: str = str(BACKUP_DIR)
    database_url: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def snapshot_path_for(self, partition_key: str) -> Path:
        """Path to raw_store.db inside the snapshot dir for `partition_key`.

        Pure derivation; does not check that the file exists. Callers
        (wiki/pending) handle the missing-file case."""
        return self.get_backup_dir() / partition_key / "raw_store.db"


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "wiki": WikiResource(database_url=dg.EnvVar("DATABASE_URL")),
    }
