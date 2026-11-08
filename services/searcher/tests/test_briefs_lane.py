"""The briefs lane: Chroma's `briefings` collection, resolved in
newsletter-assistant's brief files. Fake Chroma, real files on a temp directory."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from chromadb.errors import NotFoundError

from searcher.briefs import briefs_lane
from searcher.engine import search
from searcher.models import Item, SearchRequest, SearchResponse

FILE = "2026-10-02_how-should-we-eval-rag-0190a1b2.md"
BRIEF_ID = "0190a1b2-0000-7000-8000-000000000001"


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate(
        {"contract_version": 2, "query": "rag", "caller": "voice", "sources": ["briefs"], **kw}
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
        if self.collection is None or name != "briefings":
            raise NotFoundError(f"Collection [{name}] does not exist")
        return self.collection

    async def get_or_create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True


def _lane(hits: list[tuple[str, dict[str, Any], float]] | None, briefs_dir: Path):  # type: ignore[no-untyped-def]
    chroma = FakeChroma(None if hits is None else FakeCollection(hits))

    async def client() -> FakeChroma:
        return chroma

    return briefs_lane(client, briefs_dir)


def _run(lane, req: SearchRequest) -> list[Item]:  # type: ignore[no-untyped-def]
    return asyncio.run(lane(req, [0.0], None))


def _brief(briefs_dir: Path, name: str = FILE, **fm: Any) -> Path:
    """A brief file as newsletter-assistant writes it (`core.briefs`)."""
    briefs_dir.mkdir(exist_ok=True)
    header = {
        "brief_id": BRIEF_ID,
        "slug": name.removesuffix(".md")[11:],
        "title": "Evaluating RAG",
        "status": "drafted",
        "question": "How should we eval RAG?",
        "content_ids": [],
        "opening_moves": None,
        "related_artifacts": None,
        "drafted_at": "2026-10-02T09:30:00+00:00",
        "session_id": None,
        **fm,
    }
    path = briefs_dir / name
    path.write_text(f"---\n{yaml.safe_dump(header, sort_keys=False)}---\n\nThe brief.\n")
    return path


def _hit(name: str, chunk: str, dist: float) -> tuple[str, dict[str, Any], float]:
    """A briefings chunk as kp's indexer writes it: `local:<file>`, no id or status."""
    return chunk, {"source_ref": f"local:{name}"}, dist


def test_a_hit_resolves_to_its_brief(tmp_path: Path) -> None:
    _brief(tmp_path / "briefs")
    [item] = _run(_lane([_hit(FILE, "chunk one", 0.2)], tmp_path / "briefs"), _req())
    assert item.uri == f"kos://piece/brief/{BRIEF_ID}"
    assert item.kind == "brief"
    assert item.title == "Evaluating RAG"
    assert item.date is not None and item.date.isoformat() == "2026-10-02"
    assert item.meta == {"status": "drafted"}
    assert item.passages == ["chunk one"]
    assert item.summary is None and item.url is None and item.author is None
    assert item.matched_by[0].lane == "briefs" and item.matched_by[0].rank == 1


def test_chunks_of_one_brief_are_one_item_at_its_best_rank(tmp_path: Path) -> None:
    other = "2026-10-03_agents-0190c3d4.md"
    _brief(tmp_path / "briefs")
    _brief(tmp_path / "briefs", other, brief_id="0190c3d4-0000-7000-8000-000000000002")
    hits = [_hit(FILE, "first", 0.1), _hit(other, "other", 0.2), _hit(FILE, "second", 0.3)]
    items = _run(_lane(hits, tmp_path / "briefs"), _req())
    assert [i.uri.rsplit("/", 1)[1] for i in items] == [
        BRIEF_ID,
        "0190c3d4-0000-7000-8000-000000000002",
    ]
    assert items[0].passages == ["first", "second"]


def test_a_brief_with_a_blank_title_is_shown_by_its_file_stem(tmp_path: Path) -> None:
    _brief(tmp_path / "briefs", title="")
    [item] = _run(_lane([_hit(FILE, "chunk", 0.2)], tmp_path / "briefs"), _req())
    assert item.title == FILE.removesuffix(".md")


def test_drafted_at_read_as_a_datetime_or_without_offset_gives_the_utc_date(
    tmp_path: Path,
) -> None:
    """YAML loads an unquoted timestamp as a datetime; newsletter-assistant takes
    one with no offset as UTC."""
    path = _brief(tmp_path / "briefs")
    path.write_text(
        path.read_text().replace(
            "drafted_at: '2026-10-02T09:30:00+00:00'", "drafted_at: 2026-10-02T23:30:00-05:00"
        )
    )
    naive = "2026-10-04_naive-0190e5f6.md"
    _brief(tmp_path / "briefs", naive, brief_id="b2", drafted_at="2026-10-04T23:30:00")
    items = _run(_lane([_hit(FILE, "a", 0.1), _hit(naive, "b", 0.2)], tmp_path / "briefs"), _req())
    assert [i.date.isoformat() for i in items if i.date] == ["2026-10-03", "2026-10-04"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "no header",
        "bad yaml",
        "header not a mapping",
        "missing brief_id",
        "missing status",
        "bad drafted_at",
        "outside the directory",
        "in a subdirectory",
    ],
)
def test_a_brief_newsletter_assistant_cannot_read_is_dropped(tmp_path: Path, damage: str) -> None:
    """newsletter-assistant lists flat `*.md` in its briefs directory and reads
    one only with a header that parses and carries `brief_id`, `slug`, `title`,
    `status`, `question` and `drafted_at`. Every other hit is dropped, never
    failing the lane."""
    briefs, name = tmp_path / "briefs", FILE
    good = "2026-10-03_agents-0190c3d4.md"
    _brief(briefs, good, brief_id="good")
    path = _brief(briefs)
    if damage == "missing":
        path.unlink()
    elif damage == "no header":
        path.write_text("Just text.\n")
    elif damage == "bad yaml":
        path.write_text("---\ntitle: [unclosed\n---\n\nbody\n")
    elif damage == "header not a mapping":
        path.write_text("---\n- a list\n---\n\nbody\n")
    elif damage == "missing brief_id":
        path.write_text(path.read_text().replace(f"brief_id: {BRIEF_ID}\n", ""))
    elif damage == "missing status":
        path.write_text(path.read_text().replace("status: drafted\n", ""))
    elif damage == "bad drafted_at":
        _brief(briefs, drafted_at="last tuesday")
    elif damage == "outside the directory":
        _brief(tmp_path, "outside.md")
        name = "../outside.md"
    else:
        (briefs / "old").mkdir()
        _brief(briefs / "old", "x.md")
        name = "old/x.md"
    items = _run(_lane([_hit(name, "bad", 0.1), _hit(good, "good", 0.2)], briefs), _req())
    assert [i.uri for i in items] == ["kos://piece/brief/good"]


def _search(lane, req: SearchRequest) -> SearchResponse:  # type: ignore[no-untyped-def]
    async def embed(text: str, deadline_at: float | None) -> list[float]:
        return [0.0]

    return asyncio.run(search(req, {"briefs": lane}, embed, 5.0))


def test_since_keeps_briefs_drafted_on_or_after_it(tmp_path: Path) -> None:
    on = "2026-10-03_new-0190c3d4.md"
    _brief(tmp_path / "briefs")
    _brief(tmp_path / "briefs", on, brief_id="new", drafted_at="2026-10-03T00:10:00+00:00")
    hits = [_hit(FILE, "old", 0.1), _hit(on, "new", 0.2)]
    items = _run(_lane(hits, tmp_path / "briefs"), _req(since="2026-10-03"))
    assert [i.uri for i in items] == ["kos://piece/brief/new"]


def test_a_missing_briefs_directory_fails_the_lane(tmp_path: Path) -> None:
    resp = _search(_lane([_hit(FILE, "chunk", 0.1)], tmp_path / "briefs"), _req())
    assert resp.lanes["briefs"].outcome == "failed"
    assert not (tmp_path / "briefs").exists()


def test_an_absent_collection_fails_the_lane_and_creates_nothing(tmp_path: Path) -> None:
    _brief(tmp_path / "briefs")
    chroma = FakeChroma(None)

    async def client() -> FakeChroma:
        return chroma

    resp = _search(briefs_lane(client, tmp_path / "briefs"), _req())
    assert resp.lanes["briefs"].outcome == "failed"
    assert chroma.created is False


def test_the_brief_file_reads_are_timed_in_the_trace(tmp_path: Path) -> None:
    _brief(tmp_path / "briefs")
    resp = _search(_lane([_hit(FILE, "chunk", 0.1)], tmp_path / "briefs"), _req())
    assert isinstance(resp.trace.stages_ms["brief files"], float)


def test_a_drafted_at_out_of_range_in_utc_drops_only_that_brief(tmp_path: Path) -> None:
    """A `drafted_at` newsletter-assistant reads but that cannot be converted to
    UTC is one unreadable brief, not a failed lane."""
    briefs, good = tmp_path / "briefs", "2026-10-03_agents-0190c3d4.md"
    _brief(briefs, good, brief_id="good")
    _brief(briefs, drafted_at="0001-01-01T00:00:00+01:00")
    response = _search(_lane([_hit(FILE, "bad", 0.1), _hit(good, "good", 0.2)], briefs), _req())
    assert response.lanes["briefs"].outcome == "ok"
    assert [i.uri for i in response.items] == ["kos://piece/brief/good"]


@pytest.mark.parametrize("brief_id", [None, "", 7])
def test_a_brief_whose_brief_id_is_not_a_non_empty_string_is_dropped(
    tmp_path: Path, brief_id: object
) -> None:
    """The uri is built from `brief_id`; without a usable one the brief cannot be
    named, and two such briefs would merge into one item."""
    briefs, good = tmp_path / "briefs", "2026-10-03_agents-0190c3d4.md"
    _brief(briefs, good, brief_id="good")
    _brief(briefs, brief_id=brief_id)
    items = _run(_lane([_hit(FILE, "bad", 0.1), _hit(good, "good", 0.2)], briefs), _req())
    assert [i.uri for i in items] == ["kos://piece/brief/good"]


def test_a_drafted_at_without_an_offset_is_utc_whatever_the_machine_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """newsletter-assistant takes a naive `drafted_at` as UTC, never local time."""
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        _brief(tmp_path / "briefs", drafted_at="2026-10-04T23:30:00")
        [item] = _run(_lane([_hit(FILE, "a", 0.1)], tmp_path / "briefs"), _req())
    finally:
        monkeypatch.undo()
        time.tzset()
    assert item.date is not None and item.date.isoformat() == "2026-10-04"


@pytest.mark.parametrize("key", ["question", "slug", "title", "drafted_at"])
def test_a_brief_missing_a_required_key_is_dropped(tmp_path: Path, key: str) -> None:
    briefs, good = tmp_path / "briefs", "2026-10-03_agents-0190c3d4.md"
    _brief(briefs, good, brief_id="good")
    path = _brief(briefs)
    header = yaml.safe_load(path.read_text().split("---\n")[1])
    del header[key]
    path.write_text(f"---\n{yaml.safe_dump(header, sort_keys=False)}---\n\nThe brief.\n")
    items = _run(_lane([_hit(FILE, "bad", 0.1), _hit(good, "good", 0.2)], briefs), _req())
    assert [i.uri for i in items] == ["kos://piece/brief/good"]


def test_a_date_only_drafted_at_is_kept_with_that_date(tmp_path: Path) -> None:
    """YAML loads an unquoted `drafted_at: 2026-10-02` as a date;
    newsletter-assistant's reader keeps that brief, and its date is that day."""
    path = _brief(tmp_path / "briefs")
    path.write_text(
        path.read_text().replace(
            "drafted_at: '2026-10-02T09:30:00+00:00'", "drafted_at: 2026-10-02"
        )
    )
    [item] = _run(_lane([_hit(FILE, "a", 0.1)], tmp_path / "briefs"), _req(since="2026-10-02"))
    assert item.date is not None and item.date.isoformat() == "2026-10-02"
