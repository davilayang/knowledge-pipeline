"""SQLite layer for distill.db — the single store written by the distill_sessions DAG.

Aligns to the typed-claim provenance contract (framing §7 / architecture §5.8 /
ADR-018): provenance is an author attribute, and this store holds the `user`
class — what the user articulated, verbatim. `wiki_synthesis` (a separate DAG)
reads these and lands entity-attached ones into wiki.db.claims
(author=user, origin_type='session'); distill_sessions itself writes only here,
so it has no wiki.db dependency. Storage only — no LLM / extraction logic.

Schema:
- session_summaries  one summary row per session (the "summarise_session" task).
- user_claims      the `user` provenance class. `kind` is an articulation-ladder
                   rung (paraphrase → example → confusion → objection → transfer);
                   `verbatim_user_text` is stored unsmoothed; `entity_mention` is
                   a string (a content entity, or a project anchor for a
                   self/project `transfer` open-loop). Correction is a lineage
                   relation — a new append-only claim with `refines_id` +
                   `relation`, never a rewrite.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id   TEXT PRIMARY KEY,
    session_date TEXT,
    summary      TEXT,
    distilled_at TEXT
);

CREATE TABLE IF NOT EXISTS user_claims (
    id                 INTEGER PRIMARY KEY,
    kind               TEXT NOT NULL
        CHECK (kind IN ('paraphrase', 'example', 'confusion',
                        'objection', 'transfer')),
    verbatim_user_text TEXT NOT NULL,
    status             TEXT NOT NULL
        CHECK (status IN ('confirmed', 'open', 'superseded')),
    self_ref           INTEGER NOT NULL DEFAULT 0,
    entity_mention     TEXT,
    session_id         TEXT NOT NULL,
    event_seq_span     TEXT,
    content_source     TEXT,
    context_date       TEXT,
    refines_id         INTEGER REFERENCES user_claims(id),
    relation           TEXT CHECK (relation IN ('refines', 'supersedes')),
    note_ref           TEXT,
    last_validated     TEXT,
    display_rank       INTEGER NOT NULL DEFAULT 0
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


def upsert_session_summary(
    *, db_path: Path, session_id: str, session_date: str, summary: str
) -> None:
    """Insert or replace the one summary row for a session (distilled_at stamped)."""

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO session_summaries (session_id, session_date, summary, distilled_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                session_date = excluded.session_date,
                summary      = excluded.summary,
                distilled_at = excluded.distilled_at
            """,
            (session_id, session_date, summary, _now_iso()),
        )


def get_session_summary(*, db_path: Path, session_id: str) -> dict[str, Any] | None:
    """Return the session_summaries row as a dict, or None if absent."""

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM session_summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _decode_claim(row: sqlite3.Row) -> dict[str, Any]:
    """Expose a stored claim with self_ref as bool and event_seq_span as list[int]."""

    d = dict(row)
    d["self_ref"] = bool(d["self_ref"])
    d["event_seq_span"] = json.loads(d["event_seq_span"]) if d["event_seq_span"] else None
    return d


def insert_user_claim(
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
    refines_id: int | None = None,
    relation: str | None = None,
    note_ref: str | None = None,
    display_rank: int = 0,
) -> int:
    """Insert one user_claims row; return its id.

    A correction is a new claim carrying `refines_id` + `relation`
    ('refines' = original stays valid; 'supersedes' = original leaves recall),
    never a rewrite of the original row.
    """

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO user_claims (
                kind, verbatim_user_text, status, self_ref, entity_mention,
                session_id, event_seq_span, content_source, context_date,
                refines_id, relation, note_ref, display_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                verbatim_user_text,
                status,
                int(self_ref),
                entity_mention,
                session_id,
                json.dumps(event_seq_span) if event_seq_span is not None else None,
                content_source,
                context_date,
                refines_id,
                relation,
                note_ref,
                display_rank,
            ),
        )
        return int(cur.lastrowid)


def get_user_claims(*, db_path: Path, session_id: str) -> list[dict[str, Any]]:
    """Return all user_claims for a session, ordered by display_rank then id."""

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM user_claims WHERE session_id = ? ORDER BY display_rank, id",
            (session_id,),
        ).fetchall()
    return [_decode_claim(r) for r in rows]
