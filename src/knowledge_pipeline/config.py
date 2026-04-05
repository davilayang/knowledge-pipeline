# Paths and settings for the knowledge pipeline.

import os
from pathlib import Path

# Source: newsletter-assistant project
NEWSLETTER_ASSISTANT_DIR = Path(
    os.environ.get("NEWSLETTER_ASSISTANT_DIR", str(Path.home() / "GitHub" / "newsletter-assistant"))
).expanduser()
SOURCE_DATA_DIR = NEWSLETTER_ASSISTANT_DIR / "data"
SOURCE_RAW_STORE = SOURCE_DATA_DIR / "raw_store.db"

# Local data directories
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_DIR / "data"
BACKUP_DIR = PROJECT_DIR / "backups"

# Database files to back up from newsletter-assistant
DB_FILES = ["raw_store.db", "sessions.db"]

# Local paths
LOCAL_RAW_STORE = DATA_DIR / "raw_store.db"
CHROMA_PATH = DATA_DIR / "chroma"
CHUNKS_DIR = DATA_DIR / "chunks"
EMBEDDINGS_DIR = DATA_DIR / "embeddings"

# Backup retention
MAX_BACKUPS = 7
