"""Tests for fetcher.db schema init."""

import sqlite3

from fetcher.db import init_schema, open_connection


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]


def test_init_schema_creates_three_tables(tmp_db_path: str) -> None:
    """cache, fetches, url_aliases: three tables, no others."""
    conn = open_connection(tmp_db_path)
    try:
        init_schema(conn)
        assert _table_names(conn) == ["cache", "fetches", "url_aliases"]
    finally:
        conn.close()


def test_init_schema_is_idempotent(tmp_db_path: str) -> None:
    """Calling init_schema twice does not error or duplicate tables."""
    conn = open_connection(tmp_db_path)
    try:
        init_schema(conn)
        init_schema(conn)
        assert _table_names(conn) == ["cache", "fetches", "url_aliases"]
    finally:
        conn.close()


def test_cache_table_has_required_columns(tmp_db_path: str) -> None:
    """Schema check: cache table has the columns Phase 1 will use."""
    conn = open_connection(tmp_db_path)
    try:
        init_schema(conn)
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
