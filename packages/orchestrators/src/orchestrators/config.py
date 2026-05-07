# Paths and settings for the knowledge pipeline.
#
# TODO: migrate path/env config to pydantic-settings (BaseSettings) — gets us
# typed config, validation, .env file support, and a single Settings() singleton
# instead of the os.getenv-and-Path-coerce pattern below.

import os
from pathlib import Path

# Project root
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent

# Static dataset — pinned copy of production data for reproducible evaluation.
# Lives in the project repo, not copied from an external source at runtime.
DATASETS_DIR = PROJECT_DIR / "datasets"
SOURCE_RAW_STORE = DATASETS_DIR / "raw_store_2026-04-05.db"
# NOTE: Change to raw store affects evaluation metrics, should always version-controlled

# Working data directories (runtime artifacts, not checked in)
DATA_DIR = PROJECT_DIR / "data"
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(PROJECT_DIR / "backups")))

# Local paths
LOCAL_RAW_STORE = DATA_DIR / "raw_store.db"
CHROMA_PATH = DATA_DIR / "chroma"

# Eval results
EVAL_RESULTS_DIR = DATA_DIR / "eval_results"


def strategy_dir(strategy: str, subdir: str) -> Path:
    """Return a per-strategy data directory, e.g. data/chunks/idx_markdown_minilm/."""
    return DATA_DIR / subdir / strategy


# DAG versions — one per Dagster pipeline, all colocated here for tracking.
# Bump the matching constant whenever that pipeline's DAG logic changes; Dagster
# compares this to the code_version stored on the last materialization and shows
# downstream assets as stale until re-materialized. Decoupled from package
# versions on purpose (the version-bump skill rolls package versions on every
# release, which would otherwise mark every asset stale on every release).
BACKUP_READINGS_DAG_VERSION = "2"
SYNTHESIZE_WIKI_DAG_VERSION = "1"
# BACKUP_WIKI_DAG_VERSION = "1"        # future — wiki PG backup
# BACKUP_DAGSTER_DAG_VERSION = "1"     # future — Dagster metadata PG backup


# Backup settings
# Default to the server layout (~/newsletter-assistant/data); laptops set
# BACKUP_SOURCE_DIR=~/GitHub/newsletter-assistant/data in their shell.
BACKUP_SOURCE_DIR = Path(
    os.getenv("BACKUP_SOURCE_DIR", str(Path.home() / "newsletter-assistant" / "data"))
).expanduser()
DB_FILES = ["raw_store.db", "sessions.db"]
