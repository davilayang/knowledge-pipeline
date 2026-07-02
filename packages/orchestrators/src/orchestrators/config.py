# Paths and settings for the knowledge pipeline.

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

# Local paths
LOCAL_RAW_STORE = DATA_DIR / "raw_store.db"
LOCAL_QUEUE_DB = DATA_DIR / "queue.db"
LOCAL_WIKI_DB = DATA_DIR / "wiki.db"
LOCAL_WIKI_DIR = DATA_DIR / "wiki"


# DAG versions — one per Dagster pipeline, all colocated here for tracking.
# Bump the matching constant whenever that pipeline's DAG logic changes; Dagster
# compares this to the code_version stored on the last materialization and shows
# downstream assets as stale until re-materialized. Decoupled from package
# versions on purpose (the version-bump skill rolls package versions on every
# release, which would otherwise mark every asset stale on every release).
BACKUP_READINGS_DAG_VERSION = "7"
# BACKUP_WIKI_DAG_VERSION = "1"        # future — wiki PG backup
# BACKUP_DAGSTER_DAG_VERSION = "1"     # future — Dagster metadata PG backup
SYNC_WIKI_CURATION_DAG_VERSION = "1"
WIKI_SYNTHESIS_DAG_VERSION = "1"
POPULATE_VECTOR_STORE_DAG_VERSION = "3"
FETCH_EXTRACT_QUEUE_DAG_VERSION = "3"
TRIAGE_KNOWLEDGE_QUEUE_DAG_VERSION = "2"
# EXTRACT_QUEUED_ITEMS_DAG_VERSION = "1"  # deprecated 2026-06-01 — kept as
# breadcrumb until defs/extract_queued_items/ is fully removed in a follow-up.


# Concurrency pool serializing all writes to the single wiki.db — shared by the
# attributed-lane persist (fetch_extract_queue) and the curation sync
# (sync_wiki_curation). Re-homed here when the raw synthesize_wiki pipeline (its
# former owner) was retired; the value is kept as "synthesize-wiki" so the
# running Dagster instance's pool key doesn't change.
WIKI_WRITE_POOL = "synthesize-wiki"


# Backup settings — snapshot filenames expected in each partition dir.
# SQLite snapshots (.backup): raw/sessions are NA-owned (read from
# BACKUP_SRC_DIR); queue/wiki are kp-owned (read from this repo's DATA_DIR).
DB_FILES = ["raw_store.db", "sessions.db", "queue.db", "wiki.db"]

# Per-partition gzip-tar archives of flat-file directories. notes/ is NA-owned
# (under BACKUP_SRC_DIR); wiki/ is kp-owned (DATA_DIR/wiki — the synthesized
# entity-page .md tree plus its _index sidecar).
ARCHIVE_FILES = ["notes.tgz", "wiki.tgz"]
