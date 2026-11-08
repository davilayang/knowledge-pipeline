"""The sessions lane: past voice sessions in newsletter-assistant's sessions.db,
found through Chroma's `conversations` collection and resolved in sessions.db."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from retrievers.vector_store.chroma import COLLECTION_CONVERSATIONS

from searcher.engine import Hit, LaneFn, dense, lane, open_ro, timed
from searcher.models import Item, SearchRequest

# The producer keys each chunk to its session through `source_ref`.
_PREFIX = "sessions:"


def sessions_lane(chroma: Callable[[], Awaitable[Any]], db_path: Path) -> LaneFn:
    def resolve(session_ids: list[str]) -> dict[str, dict[str, Any]]:
        with timed("sessions.db"):
            return _resolve(db_path, session_ids)

    return lane(
        "sessions", {"dense": dense(chroma, COLLECTION_CONVERSATIONS, _session_id)}, resolve, _item
    )


def _item(req: SearchRequest, sid: str, row: dict[str, Any], hit: Hit) -> Item | None:
    if req.since is not None and row["started"].date() < req.since:
        return None
    return Item(
        uri=f"kos://piece/session/{quote(sid, safe=':')}",
        kind="session",
        # The indexer's title: the summary's first line, or `Session <first 8 of
        # the id>` when there is none; the same form stands in when a chunk carries
        # no title.
        title=str(hit.meta.get("title") or "") or f"Session {sid[:8]}",
        author=None,
        date=row["started"].date(),
        url=None,
        summary=row["summary"] or None,
        passages=[hit.chunk],
        meta={},
        matched_by=[],
    )


def _session_id(meta: dict[str, Any]) -> str:
    ref = str(meta.get("source_ref", ""))
    return ref.removeprefix(_PREFIX) if ref.startswith(_PREFIX) else ""


def _resolve(db_path: Path, session_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Stored sessions by id, read in one query; a row whose timestamps do not
    parse is left out, as newsletter-assistant cannot open it either.

    """
    conn = open_ro(db_path)
    try:
        marks = ",".join("?" * len(session_ids))
        found = conn.execute(
            f"SELECT session_id, started_at, ended_at, summary FROM sessions"
            f" WHERE session_id IN ({marks})",
            session_ids,
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[str, Any]] = {}
    for r in found:
        try:
            started = datetime.fromisoformat(r["started_at"])
            # Contract §3: dates are UTC dates. A naive timestamp is taken as UTC.
            if started.tzinfo is not None:
                started = started.astimezone(UTC)
            if r["ended_at"]:
                datetime.fromisoformat(r["ended_at"])
        except ValueError:
            continue
        out[r["session_id"]] = {"started": started, "summary": r["summary"]}
    return out
