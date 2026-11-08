"""The sessions lane: Chroma's `conversations` collection, resolved in
newsletter-assistant's sessions.db. Fake Chroma, real SQLite on a temp directory."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from chromadb.errors import NotFoundError

from searcher.engine import search
from searcher.models import Item, SearchRequest, SearchResponse
from searcher.sessions import sessions_lane


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate(
        {"contract_version": 2, "query": "rag", "caller": "mcp", "sources": ["sessions"], **kw}
    )


class FakeCollection:
    def __init__(self, hits: list[tuple[str, dict[str, Any], float]]):
        self.hits = hits

    async def query(self, **kw: Any) -> dict[str, Any]:
        hits = self.hits[: kw["n_results"]]
        return {
            "documents": [[h[0] for h in hits]],
            "metadatas": [[h[1] for h in hits]],
            "distances": [[h[2] for h in hits]],
        }


class FakeChroma:
    def __init__(self, collection: FakeCollection | None):
        self.collection, self.created = collection, False

    async def get_collection(self, name: str, **kw: Any) -> FakeCollection:
        if self.collection is None or name != "conversations":
            raise NotFoundError(f"Collection [{name}] does not exist")
        return self.collection

    async def get_or_create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True

    async def create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True


async def _embed(text: str, deadline_at: float | None) -> list[float]:
    return [0.0]


def _lane(chroma: FakeChroma, db: Path):  # type: ignore[no-untyped-def]
    async def client() -> FakeChroma:
        return chroma

    return sessions_lane(client, db)


def _run(lane, req: SearchRequest) -> list[Item]:  # type: ignore[no-untyped-def]
    async def go() -> list[Item]:
        return await lane(req, [0.0], None)

    return asyncio.run(go())


def _sessions_db(path: Path, rows: list[dict[str, str | None]]) -> Path:
    """A WAL sessions.db written and closed the way newsletter-assistant does it."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at TIMESTAMP NOT NULL,"
        " ended_at TIMESTAMP, summary TEXT)"
    )
    with conn:
        conn.executemany(
            "INSERT INTO sessions VALUES (:session_id, :started_at, :ended_at, :summary)", rows
        )
    conn.close()
    return path


def _row(session_id: str, **kw: str | None) -> dict[str, str | None]:
    return {
        "session_id": session_id,
        "started_at": "2026-10-02T09:30:00+00:00",
        "ended_at": "2026-10-02T09:50:00+00:00",
        "summary": "Talked about " + session_id,
        **kw,
    }


def _hit(
    session_id: str, chunk: str, dist: float, title: str | None = None
) -> tuple[str, dict[str, Any], float]:
    """A conversations chunk as the indexer writes it: titled by the summary's
    first line, or `Session <first 8 of the id>` when there is no summary."""
    meta: dict[str, Any] = {"source_ref": f"sessions:{session_id}"}
    meta["title"] = title if title is not None else f"Session {session_id[:8]}"
    return chunk, meta, dist


def test_a_hit_resolves_to_its_session(tmp_path: Path) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("s1")])
    [item] = _run(_lane(FakeChroma(FakeCollection([_hit("s1", "chunk one", 0.2)])), db), _req())
    assert item.uri == "kos://piece/session/s1"
    assert item.kind == "session"
    assert item.date is not None and item.date.isoformat() == "2026-10-02"
    assert item.summary == "Talked about s1"
    assert item.title == "Session s1"
    assert item.passages == ["chunk one"]
    assert item.matched_by[0].lane == "sessions" and item.matched_by[0].rank == 1


def test_chunks_of_one_session_are_one_item_at_its_best_rank(tmp_path: Path) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("s1"), _row("s2")])
    hits = [_hit("s1", "first", 0.1), _hit("s2", "other", 0.2), _hit("s1", "second", 0.3)]
    s1, s2 = _run(_lane(FakeChroma(FakeCollection(hits)), db), _req())
    assert [s1.uri, s2.uri] == ["kos://piece/session/s1", "kos://piece/session/s2"]
    assert s1.passages == ["first", "second"]
    assert (s1.matched_by[0].rank, s2.matched_by[0].rank) == (1, 2)


def test_a_session_no_longer_stored_or_unparseable_is_dropped(tmp_path: Path) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("bad", ended_at="not a time"), _row("kept")])
    hits = [_hit("gone", "x", 0.1), _hit("bad", "y", 0.2), _hit("kept", "z", 0.3)]
    items = _run(_lane(FakeChroma(FakeCollection(hits)), db), _req())
    assert [i.uri for i in items] == ["kos://piece/session/kept"]
    assert items[0].matched_by[0].rank == 1


def test_a_session_with_no_summary_yet_is_kept_with_a_null_summary(tmp_path: Path) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("a", summary=None), _row("b", summary="")])
    hits = [_hit("a", "x", 0.1), _hit("b", "y", 0.2)]
    a, b = _run(_lane(FakeChroma(FakeCollection(hits)), db), _req())
    assert (a.summary, b.summary) == (None, None)


def test_a_chunk_with_no_title_metadata_gets_the_indexers_own_form(tmp_path: Path) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("0123456789abcdef")])
    chunk, meta, dist = _hit("0123456789abcdef", "x", 0.1)
    del meta["title"]
    [item] = _run(_lane(FakeChroma(FakeCollection([(chunk, meta, dist)])), db), _req())
    assert item.title == "Session 01234567"


@pytest.mark.parametrize(
    ("since", "kept"),
    [("2026-10-02", ["s1"]), ("2026-10-01", ["s1"]), ("2026-10-03", []), (None, ["s1"])],
)
def test_since_keeps_sessions_started_on_or_after_it(
    tmp_path: Path, since: str | None, kept: list[str]
) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("s1")])  # started 2026-10-02
    items = _run(_lane(FakeChroma(FakeCollection([_hit("s1", "x", 0.1)])), db), _req(since=since))
    assert [i.uri.rsplit("/", 1)[1] for i in items] == kept


def test_a_missing_sessions_db_fails_the_lane_and_creates_no_file(tmp_path: Path) -> None:
    chroma = FakeChroma(FakeCollection([_hit("s1", "x", 0.1)]))

    async def go() -> SearchResponse:
        return await search(
            _req(), {"sessions": _lane(chroma, tmp_path / "sessions.db")}, _embed, 5.0
        )

    resp = asyncio.run(go())
    assert resp.lanes["sessions"].outcome == "failed"
    assert resp.status == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_an_absent_collection_fails_the_lane_and_creates_nothing(tmp_path: Path) -> None:
    chroma = FakeChroma(None)
    with pytest.raises(NotFoundError):
        _run(_lane(chroma, tmp_path / "sessions.db"), _req())
    assert chroma.created is False


def test_the_sessions_db_read_is_timed_in_the_trace(tmp_path: Path) -> None:
    db = _sessions_db(tmp_path / "sessions.db", [_row("s1")])
    chroma = FakeChroma(FakeCollection([_hit("s1", "x", 0.1)]))

    async def go() -> SearchResponse:
        return await search(_req(), {"sessions": _lane(chroma, db)}, _embed, 5.0)

    stages = asyncio.run(go()).trace.stages_ms
    assert isinstance(stages["sessions.db"], float)


def test_the_date_and_since_use_the_utc_date_of_the_start(tmp_path: Path) -> None:
    # 23:30 at UTC-5 is 04:30 the next day in UTC.
    db = _sessions_db(
        tmp_path / "sessions.db", [_row("s1", started_at="2026-10-02T23:30:00-05:00")]
    )
    lane = _lane(FakeChroma(FakeCollection([_hit("s1", "x", 0.1)])), db)
    [item] = _run(lane, _req(since="2026-10-03"))
    assert item.date is not None and item.date.isoformat() == "2026-10-03"
