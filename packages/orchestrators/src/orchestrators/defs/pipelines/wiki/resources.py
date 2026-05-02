# Wiki-specific Dagster resources.

import os
from pathlib import Path

import dagster as dg

from orchestrators.config import DATA_DIR, LOCAL_RAW_STORE


class WikiResource(dg.ConfigurableResource):
    """Paths and settings for the wiki synthesis pipeline.

    All durable state (wiki.processed / wiki.pages / wiki.aliases plus the
    LangGraph checkpoints) lives in Postgres now. database_url is resolved
    at call time via get_database_url() so the env can be set per-launch
    (Dagster pattern) rather than baked in at code-server import time.

    For per-deployment overrides, pass database_url=dg.EnvVar("DATABASE_URL")
    when constructing the resource — that's Dagster's idiomatic env-binding.
    The bare-string default below is just the bottom-of-the-stack fallback.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    raw_store_db_path: str = str(LOCAL_RAW_STORE)
    max_articles: int = 50  # per-run cost guardrail, 0 = no limit
    database_url: str = ""  # caller can pass dg.EnvVar(...) or a literal

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_raw_store_path(self) -> Path:
        return Path(self.raw_store_db_path)

    def get_database_url(self) -> str:
        """Resolve the Postgres URL at call time.

        Reads from the resource field first; falls back to the DATABASE_URL
        env var if the field is empty. Raises with a useful message if both
        are unset — failing fast at the asset boundary instead of leaking
        a cryptic psycopg error from inside the workflow.
        """
        url = self.database_url or os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError(
                "WikiResource needs a Postgres URL — pass database_url to the "
                "resource (use dg.EnvVar('DATABASE_URL') for env-driven config) "
                "or export DATABASE_URL in the environment."
            )
        return url
