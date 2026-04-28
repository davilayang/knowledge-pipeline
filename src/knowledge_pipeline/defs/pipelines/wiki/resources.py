# Wiki-specific Dagster resources.

from pathlib import Path

import dagster as dg

from knowledge_pipeline.config import DATA_DIR


class WikiResource(dg.ConfigurableResource):
    """Paths and settings for the wiki synthesis pipeline."""

    wiki_dir: str = str(DATA_DIR / "wiki")
    state_db_path: str = str(DATA_DIR / "wiki" / "wiki_state.db")
    aliases_path: str = str(DATA_DIR / "wiki" / "aliases.yaml")
    max_articles: int = 50  # per-run cost guardrail, 0 = no limit

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_state_db_path(self) -> Path:
        return Path(self.state_db_path)

    def get_aliases_path(self) -> Path:
        return Path(self.aliases_path)
