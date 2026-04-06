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

# Eval results
EVAL_RESULTS_DIR = DATA_DIR / "eval_results"


def strategy_dir(strategy: str, subdir: str) -> Path:
    """Return a per-strategy data directory, e.g. data/chunks/idx_markdown_minilm/."""
    return DATA_DIR / subdir / strategy


# Backup settings
BACKUP_SOURCE_DIR = Path.home() / "GitHub" / "newsletter-assistant" / "data"
DB_FILES = ["raw_store.db", "sessions.db"]
MAX_BACKUPS = 7
