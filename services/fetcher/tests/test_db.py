"""Tests for fetcher.db connection helper + domain-layer schema setup."""

import sqlite3
from pathlib import Path

from domains.fetches_store.sources import create_schema

from fetcher.db import open_connection


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]


def test_create_schema_creates_three_tables(tmp_db_path: str) -> None:
    """cache, fetches, url_aliases: three tables, no others."""
    create_schema(db_path=Path(tmp_db_path))

    conn = open_connection(tmp_db_path)
    try:
        assert _table_names(conn) == ["cache", "fetches", "url_aliases"]
    finally:
        conn.close()


def test_create_schema_is_idempotent(tmp_db_path: str) -> None:
    """Calling create_schema twice does not error or duplicate tables."""
    create_schema(db_path=Path(tmp_db_path))
    create_schema(db_path=Path(tmp_db_path))

    conn = open_connection(tmp_db_path)
    try:
        assert _table_names(conn) == ["cache", "fetches", "url_aliases"]
    finally:
        conn.close()


def test_cache_table_has_required_columns(tmp_db_path: str) -> None:
    """Schema check: cache table has the columns Phase 1 will use."""
    create_schema(db_path=Path(tmp_db_path))

    conn = open_connection(tmp_db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cache)").fetchall()}
        required = {
            "url_hash",
            "url",
            "canonical_url",
            "source_type",
            "markdown",
            "etag",
            "tier_used",
            "content_chars",
            "metadata_json",
            "tier_log_json",
            "fetched_at",
            "expires_at",
        }
        assert required <= cols, f"missing: {required - cols}"
    finally:
        conn.close()


def test_open_connection_returns_writable_db(tmp_db_path: str) -> None:
    """Sanity check: PRAGMA quick_check returns ok."""
    conn = open_connection(tmp_db_path)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
        assert result is not None
        assert result[0] == "ok"
    finally:
        conn.close()
