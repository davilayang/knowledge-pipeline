"""The search engine against the search-results contract (hub `contracts/search.md`
§2 and §3), with fake lanes: no network."""

import asyncio

import pytest

from searcher.engine import EmbedFn, LaneFn, search, time_limit
from searcher.models import Item, MatchedBy, SearchRequest, SearchResponse, Source


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate({"contract_version": 1, "query": "rag", **kw})


async def _embed(text: str, deadline_at: float | None) -> list[float]:
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


def _lane(*items: Item) -> LaneFn:
    async def run(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        return list(items)

    return run


async def _boom(
    req: SearchRequest, embedding: list[float], deadline_at: float | None
) -> list[Item]:
    raise RuntimeError("index missing")


def _search_now(
    req: SearchRequest,
    lanes: dict[Source, LaneFn],
    deadline: float | None = None,
    embed: EmbedFn = _embed,
) -> SearchResponse:
    return asyncio.run(search(req, lanes, embed, deadline))


def test_one_lane_failing_gives_partial_with_what_the_others_found() -> None:
    ok = _lane(_item("kos://piece/corpus/a", "corpus", 1, "p"))
    resp = _search_now(
        _req(caller="mcp", sources=["corpus", "notes"]), {"corpus": ok, "notes": _boom}
    )
    assert resp.status == "partial"
    assert resp.lanes["notes"].outcome == "failed"
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/a"]


def test_every_lane_failing_gives_unavailable_and_no_items() -> None:
    resp = _search_now(
        _req(caller="mcp", sources=["corpus", "notes"]), {"corpus": _boom, "notes": _boom}
    )
    assert resp.status == "unavailable"
    assert resp.items == []


def test_every_lane_ok_and_empty_gives_nothing_matched() -> None:
    resp = _search_now(
        _req(caller="mcp", sources=["corpus", "notes"]), {"corpus": _lane(), "notes": _lane()}
    )
    assert resp.status == "nothing_matched"
    assert resp.items == []


def test_a_requested_lane_with_no_implementation_is_skipped_and_gives_partial() -> None:
    ok = _lane(_item("kos://piece/corpus/a", "corpus", 1, "p"))
    resp = _search_now(_req(caller="mcp", sources=["corpus", "wiki"]), {"corpus": ok})
    assert resp.lanes["wiki"].outcome == "skipped"
    assert resp.status == "partial"
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/a"]


def test_a_piece_found_by_two_lanes_is_one_item_keeping_both_contributions() -> None:
    uri = "kos://piece/corpus/a"
    corpus = _lane(
        _item(uri, "corpus", 2, "shared"), _item("kos://piece/corpus/b", "corpus", 1, "b")
    )
    notes = _lane(_item(uri, "notes", 1, "own"), _item(uri, "notes", 3, "shared"))
    resp = _search_now(
        _req(caller="mcp", sources=["corpus", "notes"]), {"corpus": corpus, "notes": notes}
    )
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


def _slow(seconds: float, *items: Item) -> LaneFn:
    async def run(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
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


def test_a_search_with_no_deadline_waits_for_a_slow_lane() -> None:
    slow = _slow(0.3, _item("kos://piece/note/n", "notes", 1, "p"))
    resp = _search_now(_req(caller="mcp", sources=["notes"]), {"notes": slow}, deadline=None)
    assert resp.lanes["notes"].outcome == "ok"
    assert resp.status == "ok"


def test_two_lanes_on_one_query_embed_it_once() -> None:
    calls: list[str] = []

    async def counting_embed(text: str, deadline_at: float | None) -> list[float]:
        calls.append(text)
        return [0.0]

    lanes = {"corpus": _lane(), "notes": _lane()}
    asyncio.run(
        search(_req(caller="mcp", sources=["corpus", "notes"]), lanes, counting_embed, None)
    )
    assert calls == ["rag"]


def test_a_lane_cut_by_k_shows_more_candidates_than_returned() -> None:
    corpus = _lane(*(_item(f"kos://piece/corpus/{n}", "corpus", n, "p") for n in range(1, 5)))
    resp = _search_now(_req(caller="mcp", sources=["corpus"], k=2), {"corpus": corpus})
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/1", "kos://piece/corpus/2"]
    assert (resp.lanes["corpus"].candidates, resp.lanes["corpus"].returned) == (4, 2)


def test_a_piece_found_by_two_lanes_counts_in_both_lanes_returned() -> None:
    uri = "kos://piece/corpus/a"
    lanes = {
        "corpus": _lane(_item(uri, "corpus", 1, "p")),
        "notes": _lane(_item(uri, "notes", 1, "q")),
    }
    resp = _search_now(_req(caller="mcp", sources=["corpus", "notes"]), lanes)
    assert resp.lanes["corpus"].returned == 1
    assert resp.lanes["notes"].returned == 1


def test_a_lane_is_given_the_deadline_so_its_clients_can_time_out_inside_it() -> None:
    seen: list[float | None] = []

    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        seen.append(deadline_at)
        return []

    async def go(deadline: float | None) -> float:
        start = asyncio.get_running_loop().time()
        await search(_req(caller="voice", sources=["notes"]), {"notes": lane}, _embed, deadline)
        return start

    start = asyncio.run(go(0.2))
    assert seen[0] is not None and start < seen[0] <= start + 0.2 + 0.05
    asyncio.run(go(None))
    assert seen[1] is None


def test_a_lane_cut_off_by_the_deadline_is_cancelled_and_finished_before_the_answer() -> None:
    cleaned: list[bool] = []

    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        try:
            await asyncio.sleep(5)
        finally:
            cleaned.append(True)
        return []

    resp = _search_now(_req(caller="voice", sources=["notes"]), {"notes": lane}, deadline=0.1)
    assert resp.lanes["notes"].outcome == "timed_out"
    assert cleaned == [True]


def test_only_timed_out_lanes_give_unavailable() -> None:
    resp = _search_now(_req(caller="voice", sources=["notes"]), {"notes": _slow(5)}, deadline=0.1)
    assert resp.status == "unavailable"


def test_timed_out_and_skipped_lanes_give_unavailable() -> None:
    resp = _search_now(
        _req(caller="voice", sources=["notes", "wiki"]), {"notes": _slow(5)}, deadline=0.1
    )
    assert (resp.lanes["notes"].outcome, resp.lanes["wiki"].outcome) == ("timed_out", "skipped")
    assert resp.status == "unavailable"


def test_an_embedding_that_uses_up_the_deadline_times_out_every_lane_in_time() -> None:
    async def slow_embed(text: str, deadline_at: float | None) -> list[float]:
        await asyncio.sleep(5)
        return [0.0]

    async def go() -> tuple[SearchResponse, float]:
        loop = asyncio.get_running_loop()
        start = loop.time()
        resp = await search(
            _req(caller="voice", sources=["corpus", "notes"]),
            {"corpus": _lane(), "notes": _lane()},
            slow_embed,
            0.1,
        )
        return resp, loop.time() - start

    resp, elapsed = asyncio.run(go())
    assert elapsed < 1
    assert {s: lane.outcome for s, lane in resp.lanes.items()} == {
        "corpus": "timed_out",
        "notes": "timed_out",
    }


def test_the_trace_holds_each_stage_s_own_duration_and_the_code_rev(monkeypatch) -> None:
    import searcher.engine as engine

    monkeypatch.setattr(engine, "CODE_REV", "abc1234")
    resp = asyncio.run(
        search(_req(caller="voice", sources=["notes"]), {"notes": _slow(0.05)}, _embed, 1.0)
    )
    stages = resp.trace.stages_ms
    assert set(stages) == {"embed", "lanes", "merge", "total"}
    assert all(ms >= 0 for ms in stages.values())
    assert stages["lanes"] >= 40  # the slow lane's own time, not a cumulative mark
    assert stages["embed"] + stages["lanes"] + stages["merge"] <= stages["total"] + 1
    assert (resp.trace.code_rev, resp.trace.index_version) == ("abc1234", None)


def test_a_failed_lane_is_logged_with_its_traceback(caplog) -> None:
    with caplog.at_level("WARNING", logger="searcher.engine"):
        _search_now(_req(caller="mcp", sources=["notes"]), {"notes": _boom})
    record = next(r for r in caplog.records if "lane notes failed" in r.getMessage())
    assert record.exc_info and "index missing" in str(record.exc_info[1])


def test_the_embedder_is_given_the_same_deadline_as_the_lanes() -> None:
    """The embedding counts inside the deadline, so its client sets its timeout from it."""
    given: list[float | None] = []

    async def embed(text: str, deadline_at: float | None) -> list[float]:
        given.append(deadline_at)
        return [0.0]

    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        given.append(deadline_at)
        return []

    _search_now(
        _req(caller="voice", sources=["corpus"]), {"corpus": lane}, deadline=1.0, embed=embed
    )
    assert len(given) == 2 and given[0] is not None and given[0] == given[1]


def test_cancelling_a_search_cancels_its_lanes() -> None:
    """A client that hangs up must not leave lanes running after the request is gone."""
    lane_cancelled = asyncio.Event()

    async def lane(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            lane_cancelled.set()
            raise
        return []

    async def scenario() -> bool:
        outer = asyncio.create_task(
            search(_req(caller="mcp", sources=["corpus"]), {"corpus": lane}, _embed, None)
        )
        await asyncio.sleep(0.05)
        outer.cancel()
        await asyncio.gather(outer, return_exceptions=True)
        await asyncio.sleep(0)
        return lane_cancelled.is_set()

    assert asyncio.run(scenario())


def test_a_lane_slow_to_unwind_after_the_deadline_is_timed_out_and_not_waited_for() -> None:
    async def stubborn(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(1)  # unwinds slowly
            raise
        return []

    async def timed() -> tuple[SearchResponse, float]:
        loop = asyncio.get_running_loop()
        start = loop.time()
        resp = await search(
            _req(caller="voice", sources=["corpus"]), {"corpus": stubborn}, _embed, 0.1
        )
        return resp, loop.time() - start

    resp, took = asyncio.run(timed())
    assert resp.lanes["corpus"].outcome == "timed_out"
    assert took < 0.5


def test_a_lane_stopped_by_its_own_time_limit_is_timed_out_not_failed() -> None:
    """A lane bounds its network calls with `time_limit(deadline_at)`, which fires at
    the deadline, before the engine stops waiting; that is a timeout, not a failure."""

    async def bounded(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        async with time_limit(deadline_at):
            await asyncio.sleep(5)
        return []

    resp = _search_now(_req(caller="voice", sources=["notes"]), {"notes": bounded}, deadline=0.2)
    assert resp.lanes["notes"].outcome == "timed_out"


async def _times_out_now(
    req: SearchRequest, embedding: list[float], deadline_at: float | None
) -> list[Item]:
    raise TimeoutError("client read timed out")


def test_a_timeout_in_a_search_with_no_deadline_is_a_failure_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MCP has no deadline, so it never sees `timed_out`: a client timeout there,
    such as the 30 s per-call cap, is a failure."""
    resp = _search_now(_req(caller="mcp", sources=["notes"]), {"notes": _times_out_now})
    assert resp.lanes["notes"].outcome == "failed"
    assert "lane notes failed" in caplog.text


def test_a_timeout_well_before_the_deadline_is_a_failure() -> None:
    resp = _search_now(
        _req(caller="voice", sources=["notes"]), {"notes": _times_out_now}, deadline=5.0
    )
    assert resp.lanes["notes"].outcome == "failed"


def test_an_early_timeout_stays_a_failure_while_another_lane_holds_the_search_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def early(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        await asyncio.sleep(0.05)
        raise TimeoutError("client read timed out")

    resp = _search_now(
        _req(caller="voice", sources=["sessions", "corpus"]),
        {"sessions": early, "corpus": _slow(5)},
        deadline=0.3,
    )
    assert resp.lanes["sessions"].outcome == "failed"
    assert resp.lanes["corpus"].outcome == "timed_out"
    assert "lane sessions failed" in caplog.text
