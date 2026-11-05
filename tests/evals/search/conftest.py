"""A tiny on-disk snapshot in the layout the search scorer reads."""

import sqlite3
from pathlib import Path

import pytest


def _write_snapshot(root: Path) -> Path:
    root.mkdir(parents=True)
    corpus = sqlite3.connect(root / "corpus.db")
    corpus.execute("create table contents (content_id text, url text)")
    corpus.executemany(
        "insert into contents values (?, ?)",
        [
            ("medium::https://a.example/post-1", "https://a.example/post-1"),
            ("medium::https://b.example/post-1-copy", "https://b.example/post-1-copy"),
            ("web::https://c.example/other", "https://c.example/other"),
        ],
    )
    corpus.commit()
    sessions = sqlite3.connect(root / "sessions.db")
    sessions.execute("create table sessions (session_id text)")
    sessions.execute("create table tool_calls (session_id text, content_id text, tool_name text)")
    sessions.execute("insert into sessions values ('s-1')")
    sessions.execute(
        "insert into tool_calls values (?, ?, ?)",
        ("s-1", "web::https://c.example/other", "fetch_user_content"),
    )
    sessions.commit()
    (root / "notes_x/notes").mkdir(parents=True)
    (root / "briefs_x/briefs").mkdir(parents=True)
    (root / "wiki_x").mkdir()
    return root


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    return _write_snapshot(tmp_path / "snap")
