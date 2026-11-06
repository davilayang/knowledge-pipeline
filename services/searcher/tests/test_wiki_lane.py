"""The wiki lane: Chroma's `wiki` collection, resolved through kp's `_index/resolve.json`
and the rendered page files. Fake Chroma, real files on a temp directory."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from searcher.models import Item, SearchRequest
from searcher.wiki import wiki_lane


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate(
        {"contract_version": 1, "query": "rag", "caller": "mcp", "sources": ["wiki"], **kw}
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

        if self.collection is None or name != "wiki":
            raise NotFoundError(f"Collection [{name}] does not exist")
        return self.collection

    async def get_or_create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True

    async def create_collection(self, *a: Any, **kw: Any) -> None:
        self.created = True


def _wiki(
    tmp: Path,
    entities: dict[str, dict[str, Any]],
    pages: dict[str, str],
    schema_version: int = 1,
) -> Path:
    wiki = tmp / "wiki"
    (wiki / "_index").mkdir(parents=True)
    (wiki / "_index" / "resolve.json").write_text(
        json.dumps({"schema_version": schema_version, "entities": entities})
    )
    for name, text in pages.items():
        (wiki / name).write_text(text)
    return wiki


def _page(entity_id: str, title: str, summary: str, related: list[str] | None = None) -> str:
    return (
        f"---\nentity_id: {entity_id}\ntitle: {title}\nentity_type: concept\n"
        f"summary: {summary}\nrelated: {json.dumps(related or [])}\n"
        "updated_at: 2026-10-02\n---\nBody.\n"
    )


def _hit(entity_id: str, page_hash: str = "h1", dist: float = 0.2) -> tuple[str, dict, float]:
    return ("A summary.", {"content_id": entity_id, "page_hash": page_hash}, dist)


def _run(chroma: FakeChroma, wiki: Path, req: SearchRequest | None = None) -> list[Item]:
    async def client() -> FakeChroma:
        return chroma

    async def go() -> list[Item]:
        return await wiki_lane(client, wiki)(req or _req(), [0.0], None)

    return asyncio.run(go())


def test_a_hit_resolves_to_its_page(tmp_path: Path) -> None:
    wiki = _wiki(
        tmp_path,
        {
            "e_1": {
                "file": "rag-1.md",
                "name": "RAG",
                "num_sources": 3,
                "page_hash": "h1",
                "has_user_claims": True,
            },
            "e_2": {"file": "vectors-2.md", "name": "Vector search"},
        },
        {"rag-1.md": _page("e_1", "RAG", "Retrieval plus generation.", ["e_2"])},
    )
    [item] = _run(FakeChroma(FakeCollection([_hit("e_1")])), wiki)
    assert item.uri == "kos://piece/wiki/e_1"
    assert item.kind == "wiki"
    assert item.title == "RAG"
    assert item.summary == "Retrieval plus generation."
    assert item.date is None and item.url is None and item.author is None
    assert item.passages == ["A summary."]
    assert item.meta == {
        "num_sources": 3,
        "stale": False,
        "related": ["Vector search"],
        "has_user_notes": True,
    }


def test_a_pruned_entity_is_dropped(tmp_path: Path) -> None:
    wiki = _wiki(
        tmp_path,
        {"e_1": {"file": "rag-1.md", "name": "RAG", "page_hash": "h1"}},
        {"rag-1.md": _page("e_1", "RAG", "Retrieval plus generation.")},
    )
    items = _run(FakeChroma(FakeCollection([_hit("e_gone"), _hit("e_1")])), wiki)
    assert [i.uri for i in items] == ["kos://piece/wiki/e_1"]
    assert items[0].matched_by[0].rank == 1


def test_a_page_missing_unreadable_or_naming_another_entity_is_dropped(tmp_path: Path) -> None:
    wiki = _wiki(
        tmp_path,
        {
            "e_1": {"file": "gone-1.md", "name": "Gone", "page_hash": "h1"},
            "e_2": {"file": "broken-2.md", "name": "Broken", "page_hash": "h1"},
            "e_3": {"file": "other-3.md", "name": "Other", "page_hash": "h1"},
            "e_4": {"file": "ok-4.md", "name": "Ok", "page_hash": "h1"},
        },
        {
            "broken-2.md": "no frontmatter here",
            "other-3.md": _page("e_99", "Other", "Wrong id."),
            "ok-4.md": _page("e_4", "Ok", "Fine."),
        },
    )
    hits = [_hit("e_1"), _hit("e_2"), _hit("e_3"), _hit("e_4")]
    items = _run(FakeChroma(FakeCollection(hits)), wiki)
    assert [i.uri for i in items] == ["kos://piece/wiki/e_4"]


def test_a_page_rerendered_since_indexing_is_stale(tmp_path: Path) -> None:
    wiki = _wiki(
        tmp_path,
        {"e_1": {"file": "rag-1.md", "name": "RAG", "page_hash": "h2"}},
        {"rag-1.md": _page("e_1", "RAG", "Retrieval plus generation.")},
    )
    [item] = _run(FakeChroma(FakeCollection([_hit("e_1", page_hash="h1")])), wiki)
    assert item.meta["stale"] is True


def test_related_is_capped_at_four_titles(tmp_path: Path) -> None:
    related = [f"e_{n}" for n in range(2, 8)]
    entities: dict[str, dict[str, Any]] = {
        "e_1": {"file": "rag-1.md", "name": "RAG", "page_hash": "h1"}
    }
    entities |= {rid: {"file": f"{rid}.md", "name": f"T{rid}"} for rid in related}
    wiki = _wiki(tmp_path, entities, {"rag-1.md": _page("e_1", "RAG", "S.", related)})
    [item] = _run(FakeChroma(FakeCollection([_hit("e_1")])), wiki)
    assert item.meta["related"] == ["Te_2", "Te_3", "Te_4", "Te_5"]


@pytest.mark.parametrize("damage", ["missing", "not_json", "unknown_schema"])
def test_an_unreadable_resolve_fails_the_lane(tmp_path: Path, damage: str) -> None:
    wiki = _wiki(
        tmp_path,
        {"e_1": {"file": "rag-1.md", "name": "RAG", "page_hash": "h1"}},
        {"rag-1.md": _page("e_1", "RAG", "S.")},
        schema_version=99 if damage == "unknown_schema" else 1,
    )
    resolve = wiki / "_index" / "resolve.json"
    if damage == "missing":
        resolve.unlink()
    elif damage == "not_json":
        resolve.write_text("{")
    with pytest.raises((OSError, ValueError)):
        _run(FakeChroma(FakeCollection([_hit("e_1")])), wiki)


def test_older_resolve_spellings_are_read_as_newsletter_assistant_reads_them(
    tmp_path: Path,
) -> None:
    # The legacy `has_derived` key, and num_sources only in the page frontmatter.
    page = _page("e_1", "RAG", "S.").replace("summary:", "num_sources: 2\nsummary:")
    wiki = _wiki(
        tmp_path,
        {"e_1": {"file": "rag-1.md", "name": "RAG", "page_hash": "h1", "has_derived": True}},
        {"rag-1.md": page},
    )
    [item] = _run(FakeChroma(FakeCollection([_hit("e_1")])), wiki)
    assert item.meta["has_user_notes"] is True
    assert item.meta["num_sources"] == 2


def test_an_absent_collection_fails_the_lane_and_creates_nothing(tmp_path: Path) -> None:
    from chromadb.errors import NotFoundError

    chroma = FakeChroma(None)
    with pytest.raises(NotFoundError):
        _run(chroma, tmp_path / "wiki")
    assert chroma.created is False


def test_the_file_reads_are_timed_in_the_trace(tmp_path: Path) -> None:
    from searcher.engine import search

    wiki = _wiki(
        tmp_path,
        {"e_1": {"file": "rag-1.md", "name": "RAG", "page_hash": "h1"}},
        {"rag-1.md": _page("e_1", "RAG", "S.")},
    )
    chroma = FakeChroma(FakeCollection([_hit("e_1")]))

    async def client() -> FakeChroma:
        return chroma

    async def embed(text: str, deadline_at: float | None) -> list[float]:
        return [0.0]

    resp = asyncio.run(search(_req(), {"wiki": wiki_lane(client, wiki)}, embed, None))
    assert resp.status == "ok"
    assert resp.trace.stages_ms["resolve.json"] >= 0
    assert resp.trace.stages_ms["wiki pages"] >= 0
