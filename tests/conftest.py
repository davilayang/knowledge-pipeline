"""Shared pytest fixtures.

`wiki_db_path` is the path to a fresh `wiki.db` (SQLite) with the wiki schema
applied — pass it to code paths that open their own short-lived connections.

`wiki_db` yields an open sqlite3 connection to that same database for tests that
inspect or seed wiki state directly. Each test gets an empty database.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from domains.wiki.state import connect, create_schema


@pytest.fixture
def wiki_db_path(tmp_path) -> Path:
    """A fresh wiki.db file with the schema applied."""
    db_path = tmp_path / "wiki.db"
    create_schema(db_path=db_path)
    return db_path


@pytest.fixture
def wiki_db(wiki_db_path) -> Iterator[sqlite3.Connection]:
    """An open connection to a fresh wiki.db (schema applied)."""
    conn = connect(wiki_db_path)
    try:
        yield conn
    finally:
        conn.close()
