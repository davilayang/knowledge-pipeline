"""SQLite layer for the fetcher service

Three tables: cache, fetches, url_aliases.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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


def _connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with defaults for this service."""

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_plus_days(days: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _cache_key(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def _etag(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def create_schema(*, db_path: Path) -> None:
    """Create the three tables and supporting indexes if needed."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:

        # Bring up the current schema (creates tables if missing,
        # creates current indexes; both `IF NOT EXISTS`).
        conn.executescript(_SCHEMA_SQL)


def mark_orphans_failed(*, db_path: Path, error_json: str) -> int:
    """Sweep pending/running fetches → failed.

    Called at service boot: --workers=1 means any in-flight row at boot
    is orphaned (the worker process that owned its task_handles entry is gone).
    Returns rows affected.
    """

    # Mark pending fetch jobs as failed
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE fetches
                SET status = 'failed',
                    error_json = ?,
                    updated_at = ?
            WHERE status IN ('pending', 'running')
            """,
            (error_json, _now_iso()),
        )
        return cur.rowcount


# --- Cache Ops ---


def cache_lookup(*, db_path: Path, canonical_url: str) -> dict[str, Any] | None:
    """Read the cache row for a canonical URL, or None on miss/expired."""
    key = _cache_key(canonical_url)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT url_hash, url, canonical_url, source_type, markdown, etag,
                   tier_used, content_chars, metadata_json, tier_log_json,
                   fetched_at, expires_at
              FROM cache
             WHERE url_hash = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        if row[11] < _now_iso():
            conn.execute("DELETE FROM cache WHERE url_hash = ?", (key,))
            return None
        return {
            "url_hash": row[0],
            "url": row[1],
            "canonical_url": row[2],
            "source_type": row[3],
            "markdown": row[4],
            "etag": row[5],
            "tier_used": row[6],
            "content_chars": row[7],
            "metadata_json": row[8],
            "tier_log_json": row[9],
            "fetched_at": row[10],
            "expires_at": row[11],
        }


def cache_upsert(
    *,
    db_path: Path,
    canonical_url: str,
    source_type: str,
    markdown: str,
    tier_used: str,
    metadata_json: str,
    tier_log_json: str,
    ttl_days: int,
    url: str | None = None,
) -> None:
    """Insert or replace a cache row. Last-writer-wins on conflict."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO cache (
                url_hash, url, canonical_url, source_type, markdown, etag,
                tier_used, content_chars, metadata_json, tier_log_json,
                fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_hash) DO UPDATE SET
                url = excluded.url,
                canonical_url = excluded.canonical_url,
                source_type = excluded.source_type,
                markdown = excluded.markdown,
                etag = excluded.etag,
                tier_used = excluded.tier_used,
                content_chars = excluded.content_chars,
                metadata_json = excluded.metadata_json,
                tier_log_json = excluded.tier_log_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                _cache_key(canonical_url),
                url or canonical_url,
                canonical_url,
                source_type,
                markdown,
                _etag(markdown),
                tier_used,
                len(markdown),
                metadata_json,
                tier_log_json,
                _now_iso(),
                _iso_plus_days(ttl_days),
            ),
        )


# --- Job Ops ---


def insert_job(
    *,
    db_path: Path,
    job_id: str,
    batch_id: str,
    request_body: dict[str, Any],
    expires_in_hours: int = 24,
) -> str:
    """Insert a new pending fetch job. Returns `expires_at` ISO string."""
    now = _now_iso()
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fetches (
                job_id, status, request_json, batch_id, created_at, updated_at, expires_at
            )
            VALUES (?, 'pending', ?, ?, ?, ?, ?)
            """,
            (job_id, json.dumps(request_body), batch_id, now, now, expires_at),
        )
    return expires_at


def update_job(
    *,
    db_path: Path,
    job_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    """Update a fetches row's status, optionally setting result or error."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE fetches
               SET status = ?, result_json = ?, error_json = ?, updated_at = ?
             WHERE job_id = ?
            """,
            (
                status,
                json.dumps(result) if result else None,
                json.dumps(error) if error else None,
                _now_iso(),
                job_id,
            ),
        )


def get_job(*, db_path: Path, job_id: str) -> dict[str, Any] | None:
    """Read a fetches row for the GET handler. Returns None if not found."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT job_id, status, created_at, updated_at, expires_at,
                   result_json, error_json
              FROM fetches
             WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    out: dict[str, Any] = {
        "job_id": row[0],
        "status": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "expires_at": row[4],
    }
    if row[5]:
        out["result"] = json.loads(row[5])
    if row[6]:
        out["error"] = json.loads(row[6])
    return out


def get_job_status(*, db_path: Path, job_id: str) -> str | None:
    """Cheap status-only read for DELETE precheck. Returns None if not found."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM fetches WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return row[0] if row else None


# --- Canonicalize Ops ---


def canonicalize_lookup(*, db_path: Path, input_url_hash: str) -> dict[str, Any] | None:
    """Read the url_aliases row for an input URL hash, or None."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT input_url, canonical_url, redirects_json, params_stripped,
                   fetched_at, expires_at
              FROM url_aliases
             WHERE input_url_hash = ?
            """,
            (input_url_hash,),
        ).fetchone()
        if row is None:
            return None
        if row[5] < _now_iso():
            conn.execute("DELETE FROM url_aliases WHERE input_url_hash = ?", (input_url_hash,))
            return None
        return {
            "input_url": row[0],
            "canonical_url": row[1],
            "redirects_json": row[2],
            "params_stripped": row[3],
            "fetched_at": row[4],
            "expires_at": row[5],
        }


def canonicalize_upsert(
    *,
    db_path: Path,
    input_url_hash: str,
    input_url: str,
    canonical_url: str,
    redirects_json: str,
    params_stripped: str,
    ttl_days: int,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO url_aliases (
                input_url_hash, input_url, canonical_url, redirects_json,
                params_stripped, fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(input_url_hash) DO UPDATE SET
                input_url = excluded.input_url,
                canonical_url = excluded.canonical_url,
                redirects_json = excluded.redirects_json,
                params_stripped = excluded.params_stripped,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                input_url_hash,
                input_url,
                canonical_url,
                redirects_json,
                params_stripped,
                _now_iso(),
                _iso_plus_days(ttl_days),
            ),
        )
