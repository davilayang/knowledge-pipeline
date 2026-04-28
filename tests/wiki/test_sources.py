import sqlite3
from datetime import date
from pathlib import Path

from knowledge_pipeline.lib.wiki.sources import LocalFileSource, RawStoreSource


def _create_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
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
    """)
    conn.execute("""
        INSERT INTO contents (content_id, newsletter_id, source_key, title, author, content_md,
                              content_date, stored_at)
        VALUES ('abc123', 1, 'the_batch', 'RAG is All You Need', 'Author',
                '# RAG\n\nRAG is a technique...', '2026-04-01', '2026-04-01T00:00:00')
    """)
    conn.execute("""
        INSERT INTO contents (content_id, newsletter_id, source_key, title, author, content_md,
                              stored_at)
        VALUES ('def456', 1, 'boring_cash_cow', 'Building RAG Products', 'Author',
                '# Building\n\nHow to build...', '2026-04-02T00:00:00')
    """)
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


class TestLocalFileSource:
    def test_yields_items_from_md_files(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "notes.md").write_text("# My Notes\n\nSome content here.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert len(items) == 1
        assert items[0].source_type == "local_file"
        assert "Some content here" in items[0].text

    def test_title_from_frontmatter(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "doc.md").write_text("---\ntitle: My Custom Title\n---\n\nBody text.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].title == "My Custom Title"

    def test_title_from_filename(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "my_cool_notes.md").write_text("Just some text.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].title == "my cool notes"

    def test_date_from_filename_prefix(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "2026-04-21_meeting.md").write_text("Meeting notes.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].date == date(2026, 4, 21)

    def test_date_from_frontmatter(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "notes.md").write_text("---\ndate: 2026-03-15\n---\n\nContent.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].date == date(2026, 3, 15)

    def test_empty_dir(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        source = LocalFileSource(inbox_dir=inbox)
        assert source.get_items() == []

    def test_missing_dir(self, tmp_path: Path):
        source = LocalFileSource(inbox_dir=tmp_path / "nonexistent")
        assert source.get_items() == []

    def test_deterministic_item_id(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "doc.md").write_text("Same content.")

        source = LocalFileSource(inbox_dir=inbox)
        id1 = source.get_items()[0].item_id
        id2 = source.get_items()[0].item_id

        assert id1 == id2
