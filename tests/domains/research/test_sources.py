import sqlite3
from datetime import date
from pathlib import Path

import pytest
from domains.research.sources import ResearchSource


def _make_db(tmp_path: Path, *, wal: bool = True) -> Path:
    db_path = tmp_path / "research.db"
    conn = sqlite3.connect(db_path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            research_session_id TEXT NOT NULL,
            title TEXT,
            file_path TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        );
        """
    )
    conn.executescript(
        """
        INSERT INTO documents (research_session_id, title, file_path, content, created_at) VALUES
          ('rs1', 'RAG deep dive', 'data/research_output/rag.md',
           '# RAG\n\nFull content body.', '2026-04-05T10:00:00+00:00'),
          ('rs1', NULL, 'data/research_output/no_title_doc.md',
           '# Body only', '2026-04-06T11:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    return db_path


class TestGetItemIds:
    def test_returns_all_documents_oldest_first(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path))
        ids = source.get_item_ids()
        assert ids == ["1", "2"]

    def test_empty_db(self, tmp_path: Path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "research_session_id TEXT NOT NULL, title TEXT, file_path TEXT NOT NULL, "
            "content TEXT NOT NULL, created_at TIMESTAMP NOT NULL)"
        )
        conn.commit()
        conn.close()
        assert ResearchSource(db_path).get_item_ids() == []


class TestGetItem:
    def test_returns_none_for_unknown(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path))
        assert source.get_item("999") is None

    def test_returns_none_for_non_integer_id(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path))
        assert source.get_item("not-an-int") is None

    def test_populates_metadata(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path))
        item = source.get_item("1")
        assert item is not None
        assert item.item_id == "1"
        assert item.title == "RAG deep dive"
        assert item.source_type == "research"
        assert item.source_ref == "research:1:data/research_output/rag.md"
        assert item.date == date(2026, 4, 5)
        assert "Full content body" in item.text

    def test_title_falls_back_to_filename_stem(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path))
        item = source.get_item("2")
        assert item is not None
        assert item.title == "no title doc"

    def test_content_read_from_db_not_disk(self, tmp_path: Path):
        # No file ever written to data/research_output/; content comes from
        # the documents.content column.
        source = ResearchSource(_make_db(tmp_path))
        item = source.get_item("1")
        assert item is not None
        assert item.text.startswith("# RAG")


class TestGetItems:
    def test_yields_all_in_order(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path))
        items = source.get_items()
        assert [i.item_id for i in items] == ["1", "2"]


class TestWalAssertion:
    def test_raises_when_not_wal(self, tmp_path: Path):
        source = ResearchSource(_make_db(tmp_path, wal=False))
        with pytest.raises(RuntimeError, match="not in WAL mode"):
            source.get_item_ids()
