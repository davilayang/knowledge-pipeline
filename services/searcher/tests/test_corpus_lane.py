"""The corpus lane: Chroma's `contents` collection, resolved in newsletter-assistant's
corpus.db. Fake Chroma, real SQLite on a temp directory."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from chromadb.errors import NotFoundError

from searcher.corpus import corpus_lane
from searcher.models import Item, SearchRequest


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate(
        {"contract_version": 1, "query": "rag", "caller": "mcp", "sources": ["corpus"], **kw}
    )


class FakeCollection:
    def __init__(self, hits: list[tuple[str, dict[str, Any], float]], delay: float = 0.0):
        self.hits, self.delay = hits, delay

    async def query(self, **kw: Any) -> dict[str, Any]:
        await asyncio.sleep(self.delay)
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
        if self.collection is None or name != "contents":
            raise NotFoundError(f"Collection [{name}] does not exist")
        return self.collection

    async def get_or_create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True

    async def create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True


def _lane(chroma: FakeChroma, db: Path):  # type: ignore[no-untyped-def]
    async def client() -> FakeChroma:
        return chroma

    return corpus_lane(client, db)


def _run(lane, req: SearchRequest, deadline_at: float | None = None) -> list[Item]:  # type: ignore[no-untyped-def]
    async def go() -> list[Item]:
        return await lane(req, [0.0], deadline_at)

    return asyncio.run(go())


def test_an_absent_collection_fails_the_lane_and_creates_nothing(tmp_path: Path) -> None:
    chroma = FakeChroma(None)
    with pytest.raises(NotFoundError):
        _run(_lane(chroma, tmp_path / "corpus.db"), _req())
    assert chroma.created is False


def test_a_slow_chroma_query_is_stopped_at_the_deadline(tmp_path: Path) -> None:
    chroma = FakeChroma(FakeCollection([], delay=5.0))

    async def go() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        with pytest.raises(TimeoutError):
            await _lane(chroma, tmp_path / "corpus.db")(_req(), [0.0], start + 0.1)
        return loop.time() - start

    assert asyncio.run(go()) < 0.5


def _corpus_db(path: Path, rows: list[dict[str, str | None]]) -> Path:
    """A WAL corpus.db written and closed the way newsletter-assistant does it, so
    its `-wal`/`-shm` sidecars are gone when the lane opens it."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE contents (content_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',"
        " author TEXT NOT NULL DEFAULT '', url TEXT, stored_at TIMESTAMP NOT NULL)"
    )
    with conn:
        conn.executemany(
            "INSERT INTO contents VALUES (:content_id, :title, :author, :url, :stored_at)", rows
        )
    conn.close()
    assert not path.with_name(path.name + "-wal").exists()
    return path


def _row(cid: str, **kw: str | None) -> dict[str, str | None]:
    return {
        "content_id": cid,
        "title": "T " + cid,
        "author": "",
        "url": None,
        "stored_at": "2026-10-02T09:30:00+00:00",
        **kw,
    }


def _hit(cid: str, chunk: str, dist: float, **meta: str) -> tuple[str, dict[str, Any], float]:
    return chunk, {"content_id": cid, **meta}, dist


def test_a_hit_resolves_in_a_closed_wal_database(tmp_path: Path) -> None:
    db = _corpus_db(tmp_path / "corpus.db", [_row("kp_queue::a1", url="https://a")])
    chroma = FakeChroma(FakeCollection([_hit("kp_queue::a1", "chunk one", 0.2)]))
    [item] = _run(_lane(chroma, db), _req())
    assert item.uri == "kos://piece/corpus/kp_queue::a1"
    assert item.kind == "corpus"
    assert (item.title, item.url, item.author) == ("T kp_queue::a1", "https://a", None)
    assert item.date.isoformat() == "2026-10-02"
    assert item.passages == ["chunk one"]
    assert item.matched_by[0].lane == "corpus" and item.matched_by[0].rank == 1


def test_a_hit_missing_from_corpus_db_is_dropped(tmp_path: Path) -> None:
    db = _corpus_db(tmp_path / "corpus.db", [_row("b")])
    chroma = FakeChroma(FakeCollection([_hit("gone", "x", 0.1), _hit("b", "y", 0.2)]))
    items = _run(_lane(chroma, db), _req())
    assert [i.uri for i in items] == ["kos://piece/corpus/b"]
    assert items[0].matched_by[0].rank == 1


def test_a_blank_title_becomes_the_url(tmp_path: Path) -> None:
    db = _corpus_db(tmp_path / "corpus.db", [_row("c", title="", url="https://c")])
    [item] = _run(_lane(FakeChroma(FakeCollection([_hit("c", "z", 0.1)])), db), _req())
    assert item.title == "https://c"


def test_author_and_url_come_from_the_chunk_first_then_the_row(tmp_path: Path) -> None:
    """As newsletter-assistant fills them: a chunk carrying a url keeps its own
    author, even an empty one; a chunk without one takes both from the row."""
    db = _corpus_db(
        tmp_path / "corpus.db",
        [
            _row("m", author="Row M", url="https://row-m"),
            _row("n", author="Row N", url="https://n"),
        ],
    )
    hits = [_hit("m", "z", 0.1, url="https://meta-m"), _hit("n", "w", 0.2, author="Meta N")]
    m, n = _run(_lane(FakeChroma(FakeCollection(hits)), db), _req())
    assert (m.url, m.author) == ("https://meta-m", None)
    assert (n.url, n.author) == ("https://n", "Meta N")


def test_chunks_of_one_piece_are_one_item_with_each_whole_chunk_a_passage(tmp_path: Path) -> None:
    db = _corpus_db(tmp_path / "corpus.db", [_row("d"), _row("e")])
    hits = [_hit("d", "first chunk", 0.1), _hit("e", "other", 0.2), _hit("d", "second chunk", 0.3)]
    d, e = _run(_lane(FakeChroma(FakeCollection(hits)), db), _req())
    assert d.passages == ["first chunk", "second chunk"]
    assert (d.matched_by[0].rank, e.matched_by[0].rank) == (1, 2)


@pytest.mark.parametrize(
    ("since", "kept"),
    [("2026-10-02", ["d"]), ("2026-10-01", ["d"]), ("2026-10-03", []), (None, ["d"])],
)
def test_since_keeps_items_saved_on_or_after_it(
    tmp_path: Path, since: str | None, kept: list[str]
) -> None:
    db = _corpus_db(tmp_path / "corpus.db", [_row("d")])  # saved 2026-10-02
    items = _run(_lane(FakeChroma(FakeCollection([_hit("d", "x", 0.1)])), db), _req(since=since))
    assert [i.uri.rsplit("/", 1)[1] for i in items] == kept
