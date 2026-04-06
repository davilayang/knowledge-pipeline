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
_STRATEGIES: dict | None = None


def get_strategy(name: str) -> dict:
    """Load a strategy config by name from strategies.yaml."""
    global _STRATEGIES  # noqa: PLW0603
    if _STRATEGIES is None:
        import yaml

        yaml_path = Path(__file__).parent / "strategies.yaml"
        with open(yaml_path) as f:
            _STRATEGIES = yaml.safe_load(f)
    if name not in _STRATEGIES:
        available = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown strategy: {name!r}. Available: {available}")
    return {"strategy_name": name, **_STRATEGIES[name]}


# Backup settings
BACKUP_SOURCE_DIR = Path.home() / "GitHub" / "newsletter-assistant" / "data"
DB_FILES = ["raw_store.db", "sessions.db"]
MAX_BACKUPS = 7
