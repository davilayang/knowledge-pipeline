# Wiki-specific Dagster resources.

import os
from pathlib import Path

import dagster as dg

from orchestrators.config import DATA_DIR, LOCAL_RAW_STORE


class WikiResource(dg.ConfigurableResource):
    """Paths and settings for the wiki synthesis pipeline.

    All durable state (wiki.processed / wiki.pages / wiki.aliases plus the
    LangGraph checkpoints) lives in Postgres now, so the resource gained a
    database_url field. Default reads from the DATABASE_URL env var; can be
    overridden in dagster.yaml or per-run config.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    raw_store_db_path: str = str(LOCAL_RAW_STORE)
    max_articles: int = 50  # per-run cost guardrail, 0 = no limit
    database_url: str = ""  # set at __init__ if blank — see __post_init__

    def model_post_init(self, __context) -> None:
        # ConfigurableResource → BaseModel; use model_post_init to default
        # from env. We don't put the env read in the field default because
        # ConfigurableResource resolves defaults at class-definition time.
        if not self.database_url:
            object.__setattr__(self, "database_url", os.environ.get("DATABASE_URL", ""))

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_raw_store_path(self) -> Path:
        return Path(self.raw_store_db_path)

    def get_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "WikiResource needs a Postgres URL — set database_url in "
                "dagster.yaml or DATABASE_URL in the environment."
            )
        return self.database_url
