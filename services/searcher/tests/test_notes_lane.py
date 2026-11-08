"""The notes lane: Chroma's `notes` collection, resolved in newsletter-assistant's
note files. Fake Chroma, real files on a temp directory."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from searcher.engine import search
from searcher.models import Item, SearchRequest, SearchResponse
from searcher.notes import notes_lane

STEM = "2026-10-02_rag-evals-a1b2c3"


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate(
        {"contract_version": 2, "query": "rag", "caller": "mcp", "sources": ["notes"], **kw}
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
        from chromadb.errors import NotFoundError

        if self.collection is None or name != "notes":
            raise NotFoundError(f"Collection [{name}] does not exist")
        return self.collection

    async def get_or_create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True


def _lane(hits: list[tuple[str, dict[str, Any], float]] | None, notes_dir: Path):  # type: ignore[no-untyped-def]
    chroma = FakeChroma(None if hits is None else FakeCollection(hits))

    async def client() -> FakeChroma:
        return chroma

    return notes_lane(client, notes_dir)


def _run(lane, req: SearchRequest) -> list[Item]:  # type: ignore[no-untyped-def]
    return asyncio.run(lane(req, [0.0], None))


def _note(notes_dir: Path, stem: str = STEM, **fm: Any) -> Path:
    """A note file as newsletter-assistant writes it."""
    notes_dir.mkdir(exist_ok=True)
    header = {"note_id": stem, "title": "RAG evals", "date": stem[:10], **fm}
    path = notes_dir / f"{stem}.md"
    path.write_text(f"---\n{yaml.safe_dump(header, sort_keys=False)}---\n\nThe note.\n")
    return path


def _hit(
    stem: str, chunk: str, dist: float, title: str | None = "RAG evals"
) -> tuple[str, dict[str, Any], float]:
    """A notes chunk as kp's indexer writes it: `local:<file>`, and no title key
    when the note's title is blank."""
    meta: dict[str, Any] = {"source_ref": f"local:{stem}.md"}
    if title:
        meta["title"] = title
    return chunk, meta, dist


def test_a_hit_resolves_to_its_note(tmp_path: Path) -> None:
    _note(tmp_path / "notes")
    [item] = _run(_lane([_hit(STEM, "chunk one", 0.2)], tmp_path / "notes"), _req())
    assert item.uri == f"kos://piece/note/{STEM}"
    assert item.kind == "note"
    assert item.title == "RAG evals"
    assert item.date is not None and item.date.isoformat() == "2026-10-02"
    assert item.passages == ["chunk one"]
    assert item.summary is None and item.url is None and item.author is None
    assert item.matched_by[0].lane == "notes" and item.matched_by[0].rank == 1


def test_chunks_of_one_note_are_one_item_at_its_best_rank(tmp_path: Path) -> None:
    other = "2026-10-03_agents-d4e5f6"
    _note(tmp_path / "notes")
    _note(tmp_path / "notes", other)
    hits = [_hit(STEM, "first", 0.1), _hit(other, "other", 0.2), _hit(STEM, "second", 0.3)]
    items = _run(_lane(hits, tmp_path / "notes"), _req())
    assert [i.uri.rsplit("/", 1)[1] for i in items] == [STEM, other]
    assert items[0].passages == ["first", "second"]
    assert [i.matched_by[0].rank for i in items] == [1, 2]


def test_a_note_with_a_blank_title_is_shown_by_its_stem(tmp_path: Path) -> None:
    _note(tmp_path / "notes", title="")
    [item] = _run(_lane([_hit(STEM, "chunk", 0.2, title=None)], tmp_path / "notes"), _req())
    assert item.title == STEM


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "no header",
        "bad yaml",
        "header not a mapping",
        "note_id differs",
        "stray name",
        "impossible date",
    ],
)
def test_a_note_newsletter_assistant_cannot_open_is_dropped(tmp_path: Path, damage: str) -> None:
    """newsletter-assistant opens a note only by a stem of its own naming, with a
    header that parses and a `note_id` matching the stem; the indexer embeds any
    `*.md`. Every other hit is dropped, never failing the lane."""
    notes, stem = tmp_path / "notes", STEM
    good = "2026-10-03_agents-d4e5f6"
    _note(notes, good)
    path = _note(notes)
    if damage == "missing":
        path.unlink()
    elif damage == "no header":
        path.write_text("Just text.\n")
    elif damage == "bad yaml":
        path.write_text("---\ntitle: [unclosed\n---\n\nbody\n")
    elif damage == "header not a mapping":
        path.write_text("---\n- a list\n---\n\nbody\n")
    elif damage == "note_id differs":
        _note(notes, note_id="2026-10-02_other-ffffff")
    elif damage == "impossible date":
        stem = "2026-13-45_rag-evals-a1b2c3"
        _note(notes, stem)
    else:
        stem = "README"
        (notes / "README.md").write_text("---\ntitle: Readme\n---\n\nbody\n")
    items = _run(_lane([_hit(stem, "bad", 0.1), _hit(good, "good", 0.2)], notes), _req())
    assert [i.uri for i in items] == [f"kos://piece/note/{good}"]
    assert items[0].matched_by[0].rank == 1


def test_since_keeps_notes_dated_on_or_after_it(tmp_path: Path) -> None:
    before, on = "2026-10-01_old-aaaaaa", "2026-10-02_new-bbbbbb"
    _note(tmp_path / "notes", before)
    _note(tmp_path / "notes", on)
    hits = [_hit(before, "old", 0.1), _hit(on, "new", 0.2)]
    items = _run(_lane(hits, tmp_path / "notes"), _req(since="2026-10-02"))
    assert [i.uri for i in items] == [f"kos://piece/note/{on}"]


def _search(lane, req: SearchRequest) -> SearchResponse:  # type: ignore[no-untyped-def]
    async def embed(text: str, deadline_at: float | None) -> list[float]:
        return [0.0]

    return asyncio.run(search(req, {"notes": lane}, embed, 5.0))


def test_a_missing_notes_directory_fails_the_lane(tmp_path: Path) -> None:
    """With no notes directory every hit would drop, and the lane would read as
    "nothing matched"; a missing mount is a failure instead."""
    resp = _search(_lane([_hit(STEM, "chunk", 0.1)], tmp_path / "notes"), _req())
    assert resp.lanes["notes"].outcome == "failed"
    assert not (tmp_path / "notes").exists()


def test_an_absent_collection_fails_the_lane_and_creates_nothing(tmp_path: Path) -> None:
    _note(tmp_path / "notes")
    chroma = FakeChroma(None)

    async def client() -> FakeChroma:
        return chroma

    resp = _search(notes_lane(client, tmp_path / "notes"), _req())
    assert resp.lanes["notes"].outcome == "failed"
    assert chroma.created is False


def test_the_note_file_reads_are_timed_in_the_trace(tmp_path: Path) -> None:
    _note(tmp_path / "notes")
    resp = _search(_lane([_hit(STEM, "chunk", 0.1)], tmp_path / "notes"), _req())
    assert isinstance(resp.trace.stages_ms["note files"], float)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("---\n\n---\n\nbody\n", True),
        ("\n---\nnote_id: " + STEM + "\n---\n\nbody\n", False),
        ("---\nnote_id: " + STEM + "\n---", False),
        ("---\nnote_id: " + STEM + "\n---body\n", False),
    ],
    ids=["empty header", "leading newline", "no newline after closing", "text on closing line"],
)
def test_a_note_header_is_read_as_newsletter_assistant_reads_it(
    tmp_path: Path, text: str, kept: bool
) -> None:
    """newsletter-assistant's header rule (`core.notes._parse`): the file starts
    with `---\\n`, the header ends at the next `\\n---\\n`, and an empty header is
    an empty mapping, so that note is kept."""
    notes = tmp_path / "notes"
    _note(notes)
    (notes / f"{STEM}.md").write_text(text)
    items = _run(_lane([_hit(STEM, "chunk", 0.1)], notes), _req())
    assert [i.uri for i in items] == ([f"kos://piece/note/{STEM}"] if kept else [])


def test_a_dated_name_outside_newsletter_assistants_naming_is_dropped(tmp_path: Path) -> None:
    """The stem must be `<date>_<slug>-<6 hex>`; a valid date alone is not enough.
    The same pattern keeps an id taken from Chroma inside the notes directory."""
    notes, stray = tmp_path / "notes", "2026-10-02_Draft Notes"
    _note(notes)
    (notes / f"{stray}.md").write_text(f"---\nnote_id: {stray}\n---\n\nbody\n")
    items = _run(_lane([_hit(stray, "stray", 0.1), _hit(STEM, "good", 0.2)], notes), _req())
    assert [i.uri for i in items] == [f"kos://piece/note/{STEM}"]


def test_a_note_without_a_note_id_field_is_kept_under_its_stem(tmp_path: Path) -> None:
    """Notes saved before the header carried `note_id` have none; the stem stands."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / f"{STEM}.md").write_text("---\ntitle: RAG evals\n---\n\nbody\n")
    [item] = _run(_lane([_hit(STEM, "chunk", 0.1)], notes), _req())
    assert item.uri == f"kos://piece/note/{STEM}"
