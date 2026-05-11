import sqlite3
from datetime import date
from pathlib import Path

import pytest
from domains.raw_store.sources import (
    ContentRow,
    RawStoreSource,
    count_contents,
    get_content_ids,
    get_contents,
    set_vector_status,
)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with the contents table."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE contents (
            content_id TEXT PRIMARY KEY,
            newsletter_id INTEGER,
            source_key TEXT,
            content_date TEXT,
            title TEXT,
            author TEXT,
            url TEXT,
            content_md TEXT,
            scrape_status TEXT DEFAULT 'full',
            fetch_tier TEXT,
            fetch_attempts INTEGER DEFAULT 0,
            vector_status TEXT DEFAULT 'pending',
            stored_at TEXT
        )
    """
    )
    conn.execute(
        """
        INSERT INTO contents (content_id, newsletter_id, source_key, title, author, content_md,
                              vector_status, stored_at)
        VALUES ('item-1', 1, 'medium', 'Test Article', 'Author', 'Some markdown content here.',
                'pending', '2026-03-01T00:00:00')
    """
    )
    conn.execute(
        """
        INSERT INTO contents (content_id, newsletter_id, source_key, title, author, content_md,
                              vector_status, stored_at)
        VALUES ('item-2', 1, 'medium', 'Another Article', 'Author', 'More content.',
                'indexed', '2026-03-02T00:00:00')
    """
    )
    conn.commit()
    conn.close()
    return db_path


def test_get_contents_all(tmp_db: Path):
    items = get_contents(db_path=tmp_db)
    assert len(items) == 2
    assert all(isinstance(i, ContentRow) for i in items)


def test_get_contents_by_status(tmp_db: Path):
    pending = get_contents(vector_status="pending", db_path=tmp_db)
    assert len(pending) == 1
    assert pending[0].content_id == "item-1"


def test_set_vector_status(tmp_db: Path):
    set_vector_status("item-1", "indexed", db_path=tmp_db)
    items = get_contents(vector_status="indexed", db_path=tmp_db)
    assert len(items) == 2


def test_count_contents(tmp_db: Path):
    assert count_contents(db_path=tmp_db) == 2


def test_get_content_ids_returns_ids_only_ordered(tmp_db: Path):
    ids = get_content_ids(db_path=tmp_db)
    assert ids == ["item-1", "item-2"]


def _create_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE contents (
            content_id TEXT PRIMARY KEY,
            newsletter_id INTEGER,
            source_key TEXT,
            content_date TEXT,
            title TEXT,
            author TEXT,
            url TEXT,
            content_md TEXT,
            scrape_status TEXT DEFAULT 'full',
            fetch_tier TEXT,
            fetch_attempts INTEGER DEFAULT 0,
            vector_status TEXT DEFAULT 'pending',
            stored_at TEXT
        )
    """
    )
    conn.execute(
        """
        INSERT INTO contents (content_id, newsletter_id, source_key, title, author, content_md,
                              content_date, stored_at)
        VALUES ('abc123', 1, 'the_batch', 'RAG is All You Need', 'Author',
                '# RAG\n\nRAG is a technique...', '2026-04-01', '2026-04-01T00:00:00')
    """
    )
    conn.execute(
        """
        INSERT INTO contents (content_id, newsletter_id, source_key, title, author, content_md,
                              stored_at)
        VALUES ('def456', 1, 'boring_cash_cow', 'Building RAG Products', 'Author',
                '# Building\n\nHow to build...', '2026-04-02T00:00:00')
    """
    )
    conn.commit()
    conn.close()
    return db_path


class TestRawStoreSource:
    def test_yields_all_items(self, tmp_path: Path):
        db_path = _create_test_db(tmp_path)
        source = RawStoreSource(db_path=db_path)
        items = source.get_items()

        assert len(items) == 2
        assert items[0].item_id == "abc123"
        assert items[0].source_type == "raw_store"
        assert items[0].source_ref == "raw_store:abc123"

    def test_item_fields(self, tmp_path: Path):
        db_path = _create_test_db(tmp_path)
        source = RawStoreSource(db_path=db_path)
        item = source.get_items()[0]

        assert item.title == "RAG is All You Need"
        assert item.date == date(2026, 4, 1)
        assert "RAG is a technique" in item.text
