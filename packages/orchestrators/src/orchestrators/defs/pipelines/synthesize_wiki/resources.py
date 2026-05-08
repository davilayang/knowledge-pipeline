# Resources for the synthesize_wiki pipeline.

import os
from pathlib import Path

import dagster as dg

from orchestrators.config import DATA_DIR, LOCAL_RAW_STORE

from .def_config import MAX_PER_DISCOVERY_DEFAULT


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
    max_per_discovery: int = MAX_PER_DISCOVERY_DEFAULT
    database_url: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_raw_store_path(self) -> Path:
        return Path(self.raw_store_db_path)


def build_resources() -> dict[str, dg.ConfigurableResource]:
    # WIKI_MAX_PER_DISCOVERY is a dev-convenience override — set it low
    # in .env for fast iteration; prod leaves it unset and gets the
    # MAX_PER_DISCOVERY_DEFAULT cap. Per-launch overrides via the
    # launchpad still work and take precedence.
    return {
        "wiki": WikiResource(
            database_url=dg.EnvVar("DATABASE_URL"),
            max_per_discovery=int(
                os.environ.get("WIKI_MAX_PER_DISCOVERY", MAX_PER_DISCOVERY_DEFAULT)
            ),
        ),
    }
