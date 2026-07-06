"""SQLite layer for distill.db — the source of truth for distilled session memory.

Written by the distill_sessions pipeline (one producer), read by wiki_synthesis
(renders `## Your take` from user_generation) and by newsletter-assistant
(project_open_loops directly). Storage only — no LLM / extraction logic here.

Schema (v1 SPEC):
- session_digest      one summary row per session (the session summary, moved
                      off newsletter-assistant onto this pipeline).
- user_generation     the user's OWN generation about a content entity —
                      paraphrase_check / claim / judgment / connection /
                      open_question. `verbatim_user_text` is stored unsmoothed;
                      entity is a string `entity_mention` (resolved later by
                      wiki_synthesis, so this layer has no wiki.db dependency).
- project_open_loops  self/project-anchored unresolved apply-intents that don't
                      map to a content entity (e.g. "apply X to my Knowledge OS").
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_digest (
    session_id   TEXT PRIMARY KEY,
    session_date TEXT,
    summary      TEXT,
    distilled_at TEXT
);

CREATE TABLE IF NOT EXISTS user_generation (
    id                    INTEGER PRIMARY KEY,
    kind                  TEXT NOT NULL
        CHECK (kind IN ('paraphrase_check', 'claim', 'judgment',
                        'connection', 'open_question')),
    verbatim_user_text    TEXT NOT NULL,
    status                TEXT NOT NULL
        CHECK (status IN ('confirmed', 'corrected', 'open', 'superseded')),
    in_session_correction TEXT,
    self_ref              INTEGER NOT NULL DEFAULT 0,
    entity_mention        TEXT,
    session_id            TEXT NOT NULL,
    event_seq_span        TEXT,
    content_source        TEXT,
    context_date          TEXT,
    last_validated        TEXT,
    supersedes_id         INTEGER REFERENCES user_generation(id),
    display_rank          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_open_loops (
    id                 INTEGER PRIMARY KEY,
    verbatim_user_text TEXT NOT NULL,
    loop               TEXT NOT NULL,
    status             TEXT NOT NULL
        CHECK (status IN ('open', 'resolved', 'superseded')),
    self_ref           INTEGER NOT NULL DEFAULT 1,
    entity_mention     TEXT,
    session_id         TEXT NOT NULL,
    event_seq_span     TEXT,
    content_source     TEXT,
    context_date       TEXT,
    note_ref           TEXT,
    last_validated     TEXT
);
"""


def _connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with defaults for this store."""

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_schema(*, db_path: Path) -> None:
    """Create distill.db tables if absent. Idempotent."""

    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)


def upsert_session_digest(
    *, db_path: Path, session_id: str, session_date: str, summary: str
) -> None:
    """Insert or replace the one summary row for a session (distilled_at stamped)."""

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO session_digest (session_id, session_date, summary, distilled_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                session_date = excluded.session_date,
                summary      = excluded.summary,
                distilled_at = excluded.distilled_at
            """,
            (session_id, session_date, summary, _now_iso()),
        )


def get_session_digest(*, db_path: Path, session_id: str) -> dict[str, Any] | None:
    """Return the session_digest row as a dict, or None if absent."""

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM session_digest WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    """Expose a stored row with self_ref as bool and event_seq_span as list[int]."""

    d = dict(row)
    d["self_ref"] = bool(d["self_ref"])
    d["event_seq_span"] = json.loads(d["event_seq_span"]) if d["event_seq_span"] else None
    return d


def insert_user_generation(
    *,
    db_path: Path,
    kind: str,
    verbatim_user_text: str,
    status: str,
    session_id: str,
    self_ref: bool = False,
    entity_mention: str | None = None,
    event_seq_span: list[int] | None = None,
    content_source: str | None = None,
    context_date: str | None = None,
    in_session_correction: str | None = None,
    supersedes_id: int | None = None,
    display_rank: int = 0,
) -> int:
    """Insert one user_generation row; return its id."""

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO user_generation (
                kind, verbatim_user_text, status, in_session_correction,
                self_ref, entity_mention, session_id, event_seq_span,
                content_source, context_date, supersedes_id, display_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                verbatim_user_text,
                status,
                in_session_correction,
                int(self_ref),
                entity_mention,
                session_id,
                json.dumps(event_seq_span) if event_seq_span is not None else None,
                content_source,
                context_date,
                supersedes_id,
                display_rank,
            ),
        )
        return int(cur.lastrowid)


def get_user_generations(*, db_path: Path, session_id: str) -> list[dict[str, Any]]:
    """Return all user_generation rows for a session, ordered by display_rank then id."""

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM user_generation WHERE session_id = ? " "ORDER BY display_rank, id",
            (session_id,),
        ).fetchall()
    return [_decode_row(r) for r in rows]


def insert_project_open_loop(
    *,
    db_path: Path,
    verbatim_user_text: str,
    loop: str,
    status: str,
    session_id: str,
    self_ref: bool = True,
    entity_mention: str | None = None,
    event_seq_span: list[int] | None = None,
    content_source: str | None = None,
    context_date: str | None = None,
    note_ref: str | None = None,
) -> int:
    """Insert one project_open_loops row; return its id."""

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO project_open_loops (
                verbatim_user_text, loop, status, self_ref, entity_mention,
                session_id, event_seq_span, content_source, context_date, note_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verbatim_user_text,
                loop,
                status,
                int(self_ref),
                entity_mention,
                session_id,
                json.dumps(event_seq_span) if event_seq_span is not None else None,
                content_source,
                context_date,
                note_ref,
            ),
        )
        return int(cur.lastrowid)


def get_project_open_loops(*, db_path: Path, session_id: str) -> list[dict[str, Any]]:
    """Return all project_open_loops rows for a session, ordered by id."""

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM project_open_loops WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [_decode_row(r) for r in rows]
