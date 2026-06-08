"""Tests for domain-layer schema setup."""

import sqlite3
from pathlib import Path

from domains.fetches_store.sources import _connect, create_schema


def test_create_schema_creates_three_tables(tmp_db_path: str) -> None:
    path = Path(tmp_db_path)
    create_schema(db_path=path)

    with _connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "cache" in tables
    assert "fetches" in tables
    assert "url_aliases" in tables


def test_create_schema_is_idempotent(tmp_db_path: str) -> None:
    path = Path(tmp_db_path)
    create_schema(db_path=path)
    create_schema(db_path=path)  # Should not raise


def test_cache_table_has_required_columns(tmp_db_path: str) -> None:
    path = Path(tmp_db_path)
    create_schema(db_path=path)
    with _connect(path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cache)").fetchall()}
    assert "url_hash" in cols
    assert "markdown" in cols
    assert "metadata_json" in cols


def test_open_connection_returns_writable_db(tmp_db_path: str) -> None:
    path = Path(tmp_db_path)
    conn = _connect(path)
    try:
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        assert conn.execute("SELECT id FROM test").fetchone()[0] == 1
    finally:
        conn.close()
