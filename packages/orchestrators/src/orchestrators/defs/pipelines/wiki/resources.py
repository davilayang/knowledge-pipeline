# Wiki-specific Dagster resources.

from pathlib import Path

import dagster as dg

from orchestrators.config import DATA_DIR, LOCAL_RAW_STORE


class WikiResource(dg.ConfigurableResource):
    """Paths and settings for the wiki synthesis pipeline."""

    wiki_dir: str = str(DATA_DIR / "wiki")
    state_db_path: str = str(DATA_DIR / "wiki" / "wiki_state.db")
    aliases_path: str = str(DATA_DIR / "wiki" / "aliases.yaml")
    raw_store_db_path: str = str(LOCAL_RAW_STORE)
    max_articles: int = 50  # per-run cost guardrail, 0 = no limit

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_state_db_path(self) -> Path:
        return Path(self.state_db_path)

    def get_aliases_path(self) -> Path:
        return Path(self.aliases_path)

    def get_raw_store_path(self) -> Path:
        return Path(self.raw_store_db_path)
