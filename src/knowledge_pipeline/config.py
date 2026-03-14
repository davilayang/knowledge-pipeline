# Paths and settings for the knowledge pipeline.

from pathlib import Path

# Source: newsletter-assistant project
NEWSLETTER_ASSISTANT_DIR = Path.home() / "GitHub" / "newsletter-assistant"
SOURCE_DATA_DIR = NEWSLETTER_ASSISTANT_DIR / "data"

# Local data directories
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_DIR / "data"
BACKUP_DIR = PROJECT_DIR / "backups"

# Database files to back up from newsletter-assistant
DB_FILES = ["raw_store.db", "sessions.db"]

# Local paths
LOCAL_RAW_STORE = DATA_DIR / "raw_store.db"
CHROMA_PATH = DATA_DIR / "chroma"

# Backup retention
MAX_BACKUPS = 7
