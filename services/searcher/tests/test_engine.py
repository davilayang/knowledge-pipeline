"""The search engine against the search-results contract (hub `contracts/search.md`
§2 and §3), with fake lanes: no network."""

import asyncio

import pytest

from searcher.engine import EmbedFn, LaneFn, search, time_limit
from searcher.models import Item, MatchedBy, SearchRequest, SearchResponse, Source


def _req(**kw: object) -> SearchRequest:
    return SearchRequest.model_validate({"contract_version": 2, "query": "rag", **kw})


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
    deadline: float = 5.0,
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


def test_two_lanes_on_one_query_embed_it_once() -> None:
    calls: list[str] = []

    async def counting_embed(text: str, deadline_at: float | None) -> list[float]:
        calls.append(text)
        return [0.0]

    lanes = {"corpus": _lane(), "notes": _lane()}
    asyncio.run(search(_req(caller="mcp", sources=["corpus", "notes"]), lanes, counting_embed, 5.0))
    assert calls == ["rag"]


def test_a_lane_cut_by_k_shows_more_candidates_than_returned() -> None:
    corpus = _lane(*(_item(f"kos://piece/corpus/{n}", "corpus", n, "p") for n in range(1, 5)))
    resp = _search_now(_req(caller="mcp", sources=["corpus"], k=2), {"corpus": corpus})
    assert [i.uri for i in resp.items] == ["kos://piece/corpus/1", "kos://piece/corpus/2"]
    assert (resp.lanes["corpus"].candidates, resp.lanes["corpus"].returned) == (4, 2)


def test_k_caps_each_lane_and_there_is_no_overall_cut() -> None:
    """Contract §2 rules 5 and 6: `k` is the most items each lane returns; the
    response holds every lane's items, merged by best rank."""
    lanes = {
        lane: _lane(*(_item(f"kos://piece/{lane}/{n}", lane, n, "p") for n in range(1, 4)))
        for lane in ("corpus", "notes")
    }
    resp = _search_now(_req(caller="mcp", sources=["corpus", "notes"], k=2), lanes)
    assert [i.uri for i in resp.items] == [
        "kos://piece/corpus/1",
        "kos://piece/notes/1",
        "kos://piece/corpus/2",
        "kos://piece/notes/2",
    ]
    assert {s: (lane.candidates, lane.returned) for s, lane in resp.lanes.items()} == {
        "corpus": (3, 2),
        "notes": (3, 2),
    }


def test_k_per_source_overrides_k_for_the_lanes_it_names() -> None:
    lanes = {
        lane: _lane(*(_item(f"kos://piece/{lane}/{n}", lane, n, "p") for n in range(1, 5)))
        for lane in ("corpus", "notes")
    }
    req = _req(caller="voice", sources=["corpus", "notes"], k=1, k_per_source={"notes": 3})
    resp = _search_now(req, lanes)
    assert {s: lane.returned for s, lane in resp.lanes.items()} == {"corpus": 1, "notes": 3}


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

    async def go(deadline: float) -> float:
        start = asyncio.get_running_loop().time()
        await search(_req(caller="voice", sources=["notes"]), {"notes": lane}, _embed, deadline)
        return start

    start = asyncio.run(go(0.2))
    assert seen[0] is not None and start < seen[0] <= start + 0.2 + 0.05


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
            search(_req(caller="mcp", sources=["corpus"]), {"corpus": lane}, _embed, 5.0)
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


def test_a_lane_with_two_methods_looks_up_once_and_ranks_each_method_on_its_own() -> None:
    from searcher.engine import Hit, lane

    def method(*keys: str):
        async def hits(req, embedding, deadline_at, n) -> list[Hit]:
            return [Hit(k, f"{k} chunk", {}, 0.5) for k in keys][:n]

        return hits

    looked_up: list[list[str]] = []

    def resolve(ids: list[str]) -> dict[str, str]:
        looked_up.append(ids)
        return {i: i for i in ids if i != "gone"}

    def build(req, key, row, hit) -> Item:
        return _item(f"kos://piece/corpus/{key}", "corpus", 0, hit.chunk).model_copy(
            update={"matched_by": []}
        )

    run = lane(
        "corpus", {"dense": method("gone", "a", "a", "b"), "keyword": method("b")}, resolve, build
    )
    res = _search_now(_req(caller="mcp", sources=["corpus"]), {"corpus": run})

    assert looked_up == [["a", "b", "gone"]]
    assert [(i.uri, [(m.method, m.rank) for m in i.matched_by]) for i in res.items] == [
        ("kos://piece/corpus/a", [("dense", 1)]),
        ("kos://piece/corpus/b", [("keyword", 1), ("dense", 2)]),
    ]
    assert res.lanes["corpus"].candidates == 2


def _fill_lane(chunks_per_piece: int, pieces: int | None = None):
    """A one-method lane over an index where each piece has `chunks_per_piece`
    consecutive chunks, `pieces` of them (None: unlimited). Records each fetch size."""
    from searcher.engine import Hit, lane

    sizes: list[int] = []

    async def dense(req, embedding, deadline_at, n) -> list[Hit]:
        sizes.append(n)
        total = n if pieces is None else min(n, pieces * chunks_per_piece)
        return [Hit(f"p{i // chunks_per_piece}", f"c{i}", {}, 0.5) for i in range(total)]

    def build(req, key, row, hit) -> Item:
        return _item(f"kos://piece/corpus/{key}", "corpus", 0, hit.chunk).model_copy(
            update={"matched_by": []}
        )

    return lane("corpus", {"dense": dense}, lambda ids: {i: i for i in ids}, build), sizes


def test_a_lane_short_of_its_limit_after_duplicates_fetches_again_at_double_size() -> None:
    """Contract §2 rule 5: a lane fills its limit with distinct pieces, fetching
    past duplicate chunks with the same embedding."""
    run, sizes = _fill_lane(chunks_per_piece=4)
    resp = _search_now(_req(caller="voice", sources=["corpus"], k=3), {"corpus": run})
    assert sizes == [3, 6, 12]
    assert [i.uri for i in resp.items] == [f"kos://piece/corpus/p{n}" for n in range(3)]


def test_a_lane_stops_fetching_at_100_chunks() -> None:
    run, sizes = _fill_lane(chunks_per_piece=1000)
    resp = _search_now(_req(caller="voice", sources=["corpus"], k=3), {"corpus": run})
    assert sizes == [3, 6, 12, 24, 48, 96, 100]
    assert resp.lanes["corpus"].returned == 1


def test_a_lane_whose_index_runs_out_does_not_fetch_again() -> None:
    run, sizes = _fill_lane(chunks_per_piece=2, pieces=1)
    resp = _search_now(_req(caller="voice", sources=["corpus"], k=3), {"corpus": run})
    assert sizes == [3]
    assert resp.lanes["corpus"].returned == 1


def test_a_stage_timed_in_every_fetch_round_records_their_total() -> None:
    """A lane that fetches again looks its source up again; the trace shows the
    lookups' total, since the trace is where blocking reads are measured."""
    import time

    from searcher.engine import Hit, lane, timed

    async def dense(req, embedding, deadline_at, n) -> list[Hit]:
        return [Hit(f"p{i // 4}", f"c{i}", {}, 0.5) for i in range(n)]

    def resolve(ids):
        with timed("source"):
            time.sleep(0.02)
        return {i: i for i in ids}

    def build(req, key, row, hit) -> Item:
        return _item(f"kos://piece/corpus/{key}", "corpus", 0, hit.chunk).model_copy(
            update={"matched_by": []}
        )

    run = lane("corpus", {"dense": dense}, resolve, build)
    resp = _search_now(_req(caller="voice", sources=["corpus"], k=3), {"corpus": run})
    assert resp.trace.stages_ms["source"] >= 55


def test_an_item_carries_at_most_three_passages_the_best_ranked() -> None:
    """Contract §2: an item's passages, their union across contributions, are cut to
    the 3 best ranked, so a piece with many matching chunks cannot flood a caller."""
    item = _item("kos://piece/corpus/a", "corpus", 1, "c0").model_copy(
        update={"passages": ["c0", "c1", "c2", "c3", "c4"]}
    )
    resp = _search_now(_req(caller="voice", sources=["corpus"]), {"corpus": _lane(item)})
    assert resp.items[0].passages == ["c0", "c1", "c2"]
