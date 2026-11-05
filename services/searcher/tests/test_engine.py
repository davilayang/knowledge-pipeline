"""The search engine against the search-results contract (hub `contracts/search.md`
§2 and §3), with fake lanes: no network."""

import asyncio

from searcher.engine import search
from searcher.models import Item, MatchedBy, SearchRequest


def _req(**kw) -> SearchRequest:
    return SearchRequest.model_validate({"contract_version": 1, "query": "rag", **kw})


async def _embed(text: str) -> list[float]:
    return [0.0]


def _item(uri: str, lane: str, rank: int, passage: str) -> Item:
    return Item(
        uri=uri,
        kind="corpus" if lane == "corpus" else "note",
        title=uri,
        author=None,
        date=None,
        url=None,
        summary=None,
        passages=[passage],
        meta={},
        matched_by=[MatchedBy(query="rag", lane=lane, method="dense", rank=rank, score=0.5)],
    )


def _lane(*items: Item):
    async def run(query: str, embedding: list[float]) -> list[Item]:
        return list(items)

    return run


async def _boom(query: str, embedding: list[float]) -> list[Item]:
    raise RuntimeError("index missing")


def _run(req, lanes, deadline=None):
    return asyncio.run(search(req, lanes, _embed, deadline))


def test_one_lane_failing_gives_partial_with_what_the_others_found() -> None:
    ok = _lane(_item("kos://piece/corpus/a", "corpus", 1, "p"))
    resp = _run(_req(caller="mcp", sources=["corpus", "notes"]), {"corpus": ok, "notes": _boom})
    assert resp.status == "partial"
    assert resp.lanes["notes"].outcome == "failed"
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/a"]


def test_every_lane_failing_gives_unavailable_and_no_items() -> None:
    resp = _run(_req(caller="mcp", sources=["corpus", "notes"]), {"corpus": _boom, "notes": _boom})
    assert resp.status == "unavailable"
    assert resp.items == []


def test_every_lane_ok_and_empty_gives_nothing_matched() -> None:
    resp = _run(
        _req(caller="mcp", sources=["corpus", "notes"]), {"corpus": _lane(), "notes": _lane()}
    )
    assert resp.status == "nothing_matched"
    assert resp.items == []


def test_a_requested_lane_with_no_implementation_is_skipped_and_gives_partial() -> None:
    ok = _lane(_item("kos://piece/corpus/a", "corpus", 1, "p"))
    resp = _run(_req(caller="mcp", sources=["corpus", "wiki"]), {"corpus": ok})
    assert resp.lanes["wiki"].outcome == "skipped"
    assert resp.status == "partial"
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/a"]


def test_a_piece_found_by_two_lanes_is_one_item_keeping_both_contributions() -> None:
    uri = "kos://piece/corpus/a"
    corpus = _lane(
        _item(uri, "corpus", 2, "shared"), _item("kos://piece/corpus/b", "corpus", 1, "b")
    )
    notes = _lane(_item(uri, "notes", 1, "own"), _item(uri, "notes", 3, "shared"))
    resp = _run(_req(caller="mcp", sources=["corpus", "notes"]), {"corpus": corpus, "notes": notes})
    assert [i.uri for i in resp.items].count(uri) == 1
    merged = next(i for i in resp.items if i.uri == uri)
    assert [(m.lane, m.rank) for m in merged.matched_by] == [
        ("notes", 1),
        ("corpus", 2),
        ("notes", 3),
    ]
    assert merged.passages == ["own", "shared"]
    assert resp.lanes["corpus"].candidates == 2
    assert resp.lanes["notes"].candidates == 1


def _slow(seconds: float, *items: Item):
    async def run(query: str, embedding: list[float]) -> list[Item]:
        await asyncio.sleep(seconds)
        return list(items)

    return run


def test_a_lane_slower_than_the_deadline_is_timed_out_and_the_answer_returns_in_time() -> None:
    fast = _lane(_item("kos://piece/corpus/a", "corpus", 1, "p"))
    loop = asyncio.new_event_loop()
    start = loop.time()
    resp = loop.run_until_complete(
        search(
            _req(caller="voice", sources=["corpus", "notes"]),
            {"corpus": fast, "notes": _slow(5)},
            _embed,
            deadline=0.2,
        )
    )
    elapsed = loop.time() - start
    loop.close()
    assert elapsed < 1
    assert resp.lanes["notes"].outcome == "timed_out"
    assert resp.status == "partial"
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/a"]


def test_an_mcp_search_has_no_deadline_and_waits_for_a_slow_lane() -> None:
    slow = _slow(0.3, _item("kos://piece/note/n", "notes", 1, "p"))
    resp = _run(_req(caller="mcp", sources=["notes"]), {"notes": slow}, deadline=None)
    assert resp.lanes["notes"].outcome == "ok"
    assert resp.status == "ok"


def test_two_lanes_on_one_query_embed_it_once() -> None:
    calls: list[str] = []

    async def counting_embed(text: str) -> list[float]:
        calls.append(text)
        return [0.0]

    lanes = {"corpus": _lane(), "notes": _lane()}
    asyncio.run(
        search(_req(caller="mcp", sources=["corpus", "notes"]), lanes, counting_embed, None)
    )
    assert calls == ["rag"]
