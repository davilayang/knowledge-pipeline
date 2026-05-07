# Resources for the synthesize_wiki pipeline.

from pathlib import Path

import dagster as dg

from orchestrators.config import DATA_DIR, LOCAL_RAW_STORE

from .def_config import MAX_ARTICLES_DEFAULT


class WikiResource(dg.ConfigurableResource):
    """Paths and per-launch knobs for wiki synthesis.

    All durable state (wiki.processed / wiki.pages / wiki.aliases plus the
    LangGraph checkpoints) lives in the Postgres pointed to by
    `database_url`. Resolved at run init via dg.EnvVar — an unset
    DATABASE_URL fails fast rather than leaking a cryptic psycopg error
    from inside the workflow.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    raw_store_db_path: str = str(LOCAL_RAW_STORE)
    max_articles: int = MAX_ARTICLES_DEFAULT
    database_url: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_raw_store_path(self) -> Path:
        return Path(self.raw_store_db_path)


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "wiki": WikiResource(database_url=dg.EnvVar("DATABASE_URL")),
    }
