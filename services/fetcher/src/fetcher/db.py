"""SQLite connection helper for the fetcher service.

Schema lives in ``domains.fetches_store.sources`` (``create_schema``,
``mark_orphans_failed``); that store keeps its own ``_connect`` private — same
convention as ``queue_store`` and ``raw_store``. Service-side callers that run
inline SQL (workers, endpoints, cache) open their connection through this
module instead of reaching into the domain layer.
"""

import sqlite3
from pathlib import Path


def open_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with the fetcher's expected PRAGMAs."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
