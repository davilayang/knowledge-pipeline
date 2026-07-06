"""SessionsSource — read voice-session transcripts from newsletter-assistant's
``sessions.db``.

Schema lock — scope: only the tables this reader consumes
(``sessions`` + ``events``). The same DB also holds the legacy ``turns``
table (kept alive by newsletter-assistant's dual-write) plus ``tool_calls``,
``llm_events``, ``facts``, ``facts_history`` and ``lens_log`` (writer-side,
see ``newsletter-assistant`` ``session_store.py``); their schema is
irrelevant here and intentionally not pinned. ``turns`` is deliberately not
read — it is unfiltered (tool_call / tool rows interleaved) and legacy;
``events`` is the canonical stream and lets us drop the tool machinery.

Schema lock (newsletter-assistant ``packages/knowledge/src/knowledge/session_store.py``):

    sessions(
        session_id    TEXT PRIMARY KEY,
        started_at    TIMESTAMP NOT NULL,        -- ISO 8601 UTC
        ended_at      TIMESTAMP,                  -- NULL while session is live
        summary       TEXT,                       -- post-session natural-language summary
        prompt_n_docstrings_hash  TEXT,
        model_config_hash         TEXT
    )

    events(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id    TEXT NOT NULL REFERENCES sessions(session_id),
        seq           INTEGER NOT NULL,           -- per-session ordering
        timestamp     TEXT NOT NULL,              -- ISO 8601 UTC
        type          TEXT NOT NULL,              -- user_msg|assistant_msg|tool_call|tool_result
        content       TEXT,
        tool_call_ref INTEGER,                    -- unused here
        group_id      TEXT                        -- unused here
    )

Only ``user_msg`` / ``assistant_msg`` events are indexed; ``tool_call`` /
``tool_result`` are excluded. Connection runs in WAL mode (asserted on
connect). Completion gate is ``WHERE ended_at IS NOT NULL`` — we never index
a live session.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from domains.types import IngestItem

# Sentinel for the serialized turn format consumed by turn_grouping_chunker.
# Each turn block starts with one of these lines on its own line; chunkers
# (or any consumer) split on this marker to recover turn boundaries.
TURN_MARKER_PREFIX = "<<<TURN"


class SessionsSource:
    """Yields IngestItems from a newsletter-assistant ``sessions.db``."""

    def __init__(self, db_path: Path):
        self._db_path = db_path

    def get_item_ids(self) -> list[str]:
        """Session IDs of all ended sessions, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id FROM sessions WHERE ended_at IS NOT NULL ORDER BY started_at"
            ).fetchall()
        return [r["session_id"] for r in rows]

    def get_item(self, item_id: str) -> IngestItem | None:
        with self._connect() as conn:
            session = conn.execute(
                "SELECT session_id, started_at, ended_at, summary "
                "FROM sessions WHERE session_id = ? AND ended_at IS NOT NULL",
                (item_id,),
            ).fetchone()
            if session is None:
                return None
            # Read the canonical ``events`` stream, not the legacy ``turns``
            # table. Filter to the two dialogue event types — tool_call /
            # tool_result events are agent machinery (raw tool JSON, fetched
            # payloads that already live in the ``contents`` collection) and
            # would pollute recall. Map the event ``type`` back to the ``role``
            # the turn serializer expects.
            turns = conn.execute(
                "SELECT CASE type WHEN 'user_msg' THEN 'user' ELSE 'assistant' END AS role, "
                "content, timestamp FROM events "
                "WHERE session_id = ? AND type IN ('user_msg', 'assistant_msg') "
                "ORDER BY seq",
                (item_id,),
            ).fetchall()
        return _to_item(session, turns)

    def get_items(self) -> list[IngestItem]:
        items: list[IngestItem] = []
        for item_id in self.get_item_ids():
            item = self.get_item(item_id)
            if item is not None:
                items.append(item)
        return items

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(
                    f"sessions.db at {self._db_path} is not in WAL mode "
                    f"(journal_mode={mode!r}); concurrent reads would block writers."
                )
            yield conn
        finally:
            conn.close()


def _to_item(session: sqlite3.Row, turns: list[sqlite3.Row]) -> IngestItem:
    started_at = datetime.fromisoformat(session["started_at"])
    summary = session["summary"]
    title = (
        (summary or "").strip().splitlines()[0][:120]
        if summary
        else (f"Session {session['session_id'][:8]}")
    )
    return IngestItem(
        item_id=session["session_id"],
        title=title,
        date=started_at.date(),
        text=_serialize_turns(turns),
        source_type="sessions",
        source_ref=f"sessions:{session['session_id']}",
        started_at=started_at,
    )


def _serialize_turns(turns: list[sqlite3.Row]) -> str:
    """Serialize turns to a marker-delimited format the turn_grouping chunker
    can split back into turns. Format::

        <<<TURN role=user ts=2026-04-01T14:32:01+00:00>>>
        What is RAG?
        <<<TURN role=assistant ts=2026-04-01T14:32:05+00:00>>>
        RAG stands for retrieval-augmented generation...

    The marker line is unlikely to collide with natural turn content; an
    accidental collision degrades chunk boundaries but does not break parsing.
    """
    parts: list[str] = []
    for t in turns:
        parts.append(f"{TURN_MARKER_PREFIX} role={t['role']} ts={t['timestamp']}>>>")
        parts.append(t["content"].rstrip())
    return "\n".join(parts)
