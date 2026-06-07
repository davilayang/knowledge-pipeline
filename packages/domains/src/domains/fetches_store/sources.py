"""SQLite layer for the fetcher service

Three tables: cache, fetches, url_aliases.
"""

import sqlite3
from pathlib import Path

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cache (
    url_hash       TEXT PRIMARY KEY,
    url            TEXT NOT NULL,
    canonical_url  TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    markdown       TEXT NOT NULL,
    etag           TEXT NOT NULL,
    tier_used      TEXT NOT NULL,
    content_chars  INTEGER NOT NULL,
    metadata_json  TEXT NOT NULL,
    tier_log_json  TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS cache_expires_at ON cache(expires_at);

CREATE TABLE IF NOT EXISTS fetches (
    job_id         TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    request_json   TEXT NOT NULL,
    batch_id       TEXT,
    result_json    TEXT,
    error_json     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS fetches_status ON fetches(status);
CREATE INDEX IF NOT EXISTS fetches_batch_id ON fetches(batch_id);
CREATE INDEX IF NOT EXISTS fetches_expires_at ON fetches(expires_at);

CREATE TABLE IF NOT EXISTS url_aliases (
    input_url_hash    TEXT PRIMARY KEY,
    input_url         TEXT NOT NULL,
    canonical_url     TEXT NOT NULL,
    redirects_json    TEXT NOT NULL,
    params_stripped   TEXT NOT NULL,
    fetched_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS url_aliases_expires_at ON url_aliases(expires_at);
"""


def open_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with defaults for this service."""

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(*, db_path: Path) -> None:
    """Create the three tables and supporting indexes if needed."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open_connection(db_path) as conn:

        # Bring up the current schema (creates tables if missing,
        # creates current indexes; both `IF NOT EXISTS`).
        conn.executescript(_SCHEMA_SQL)
