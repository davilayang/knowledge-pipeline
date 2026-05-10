import sqlite3
from datetime import date
from pathlib import Path

import pytest
from domains.sessions.sources import SessionsSource


def _make_db(tmp_path: Path, *, wal: bool = True) -> Path:
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(db_path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            started_at TIMESTAMP NOT NULL,
            ended_at TIMESTAMP,
            summary TEXT
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        (
            "s_done",
            "2026-04-01T14:00:00+00:00",
            "2026-04-01T14:10:00+00:00",
            "A summary line.\nSecond line.",
        ),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        ("s_live", "2026-04-02T09:00:00+00:00", None, None),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        (
            "s_no_summary",
            "2026-04-03T08:00:00+00:00",
            "2026-04-03T08:05:00+00:00",
            None,
        ),
    )
    conn.executescript(
        """
        INSERT INTO turns (session_id, role, content, timestamp) VALUES
          ('s_done', 'user', 'What is RAG?', '2026-04-01T14:01:00+00:00'),
          ('s_done', 'assistant', 'Retrieval-augmented generation.',
           '2026-04-01T14:01:30+00:00'),
          ('s_live', 'user', 'Hello?', '2026-04-02T09:00:30+00:00'),
          ('s_no_summary', 'user', 'Hi', '2026-04-03T08:01:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    return db_path


class TestGetItemIds:
    def test_excludes_live_sessions(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        ids = source.get_item_ids()
        assert "s_live" not in ids
        assert set(ids) == {"s_done", "s_no_summary"}

    def test_ordered_oldest_first(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        assert source.get_item_ids() == ["s_done", "s_no_summary"]


class TestGetItem:
    def test_returns_none_for_live_session(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        assert source.get_item("s_live") is None

    def test_returns_none_for_unknown(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        assert source.get_item("nope") is None

    def test_populates_metadata(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        item = source.get_item("s_done")
        assert item is not None
        assert item.item_id == "s_done"
        assert item.source_type == "sessions"
        assert item.source_ref == "sessions:s_done"
        assert item.date == date(2026, 4, 1)
        assert item.started_at is not None
        assert item.title == "A summary line."

    def test_title_falls_back_when_summary_missing(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        item = source.get_item("s_no_summary")
        assert item is not None
        assert item.title.startswith("Session ")

    def test_text_serializes_turns_in_order(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        item = source.get_item("s_done")
        assert item is not None
        assert item.text.count("<<<TURN") == 2
        u_idx = item.text.find("role=user")
        a_idx = item.text.find("role=assistant")
        assert 0 <= u_idx < a_idx
        assert "What is RAG?" in item.text
        assert "Retrieval-augmented generation." in item.text


class TestGetItems:
    def test_yields_only_ended_sessions(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path))
        items = source.get_items()
        ids = [i.item_id for i in items]
        assert "s_live" not in ids
        assert ids == ["s_done", "s_no_summary"]


class TestWalAssertion:
    def test_raises_when_not_wal(self, tmp_path: Path):
        source = SessionsSource(_make_db(tmp_path, wal=False))
        with pytest.raises(RuntimeError, match="not in WAL mode"):
            source.get_item_ids()
