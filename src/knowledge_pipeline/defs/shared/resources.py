# Shared Dagster resources used across all RAG strategies.

from pathlib import Path

import dagster as dg

from knowledge_pipeline.config import LOCAL_RAW_STORE, SOURCE_RAW_STORE, strategy_dir


class RawStoreResource(dg.ConfigurableResource):
    """Read-only access to raw_store.db (local copy + source for status writes)."""

    db_path: str = str(LOCAL_RAW_STORE)
    source_db_path: str = str(SOURCE_RAW_STORE)

    def get_path(self) -> Path:
        return Path(self.db_path)

    def get_source_path(self) -> Path:
        return Path(self.source_db_path)


class StrategyPathsResource(dg.ConfigurableResource):
    """Per-strategy data directories to avoid collisions between strategies."""

    strategy_name: str

    @property
    def chunks_dir(self) -> Path:
        return strategy_dir(self.strategy_name, "chunks")

    @property
    def embeddings_dir(self) -> Path:
        return strategy_dir(self.strategy_name, "embeddings")
