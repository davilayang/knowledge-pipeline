# Paths and settings for the knowledge pipeline.

from pathlib import Path

# Project root
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

# Static dataset — pinned copy of production data for reproducible evaluation.
# Lives in the project repo, not copied from an external source at runtime.
DATASETS_DIR = PROJECT_DIR / "datasets"
SOURCE_RAW_STORE = DATASETS_DIR / "raw_store_2026-04-05.db"
# NOTE: Change to raw store affects evaluation metrics, should always version-controlled

# Working data directories (runtime artifacts, not checked in)
DATA_DIR = PROJECT_DIR / "data"
BACKUP_DIR = PROJECT_DIR / "backups"

# Local paths
LOCAL_RAW_STORE = DATA_DIR / "raw_store.db"
CHROMA_PATH = DATA_DIR / "chroma"
CHUNKS_DIR = DATA_DIR / "chunks"
EMBEDDINGS_DIR = DATA_DIR / "embeddings"

# Eval results
EVAL_RESULTS_DIR = DATA_DIR / "eval_results"


def strategy_dir(strategy: str, subdir: str) -> Path:
    """Return a per-strategy data directory, e.g. data/chunks/rag_0_baseline/."""
    return DATA_DIR / subdir / strategy


# Strategy configurations (loaded from strategies.yaml)
_STRATEGIES_YAML: dict | None = None


def _load_strategies_yaml() -> dict:
    """Load and cache strategies.yaml."""
    global _STRATEGIES_YAML  # noqa: PLW0603
    if _STRATEGIES_YAML is None:
        import yaml

        yaml_path = Path(__file__).parent / "strategies.yaml"
        with open(yaml_path) as f:
            _STRATEGIES_YAML = yaml.safe_load(f)
    return _STRATEGIES_YAML


def get_strategy(name: str) -> dict:
    """Load an index strategy config by name."""
    strategies = _load_strategies_yaml()["index_strategies"]
    if name not in strategies:
        available = ", ".join(sorted(strategies))
        raise ValueError(f"Unknown index strategy: {name!r}. Available: {available}")
    return {"strategy_name": name, **strategies[name]}


def get_eval_combos() -> list[str]:
    """Load eval combos from strategies.yaml."""
    return _load_strategies_yaml()["eval_combos"]


def get_embedding_model_for_collection(collection_name: str) -> str:
    """Look up the embedding model for a collection name."""
    for cfg in _load_strategies_yaml()["index_strategies"].values():
        if cfg["collection_name"] == collection_name:
            return cfg["embedding_model"]
    raise ValueError(f"No strategy found for collection: {collection_name!r}")


# Backup settings
BACKUP_SOURCE_DIR = Path.home() / "GitHub" / "newsletter-assistant" / "data"
DB_FILES = ["raw_store.db", "sessions.db"]
MAX_BACKUPS = 7
