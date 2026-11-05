"""One search, run as the hub contract `contracts/search.md` §3 defines it: lanes
run concurrently, each reports an outcome, and the status follows from them."""

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping

from searcher.models import (
    SOURCES,
    Item,
    Lane,
    SearchRequest,
    SearchResponse,
    Source,
    Status,
    Trace,
)

# A lane gets the query and its embedding and returns its pieces, best first.
LaneFn = Callable[[str, list[float]], Awaitable[list[Item]]]
Embedder = Callable[[str], Awaitable[list[float]]]

# ponytail: one default until per-caller defaults are set from today's callers.
K_DEFAULT = 10


async def _run(lane: LaneFn, query: str, embedding: asyncio.Task[list[float]]) -> list[Item]:
    # Shielded: one lane timing out must not cancel the embedding the others share.
    return await lane(query, await asyncio.shield(embedding))


def _status(lanes: dict[Source, Lane], items: list[Item]) -> Status:
    if not any(lane.outcome == "ok" for lane in lanes.values()):
        return "unavailable"
    if any(lane.outcome != "ok" for lane in lanes.values()):
        return "partial"
    return "ok" if items else "nothing_matched"


async def search(
    req: SearchRequest,
    lanes: Mapping[Source, LaneFn],
    embed: Embedder,
    deadline: float | None,
) -> SearchResponse:
    """Answer one request. `deadline` is seconds for the whole search, embedding
    included; past it, unfinished lanes are `timed_out`. None waits for every lane."""
    loop = asyncio.get_running_loop()
    began = loop.time()
    stages: dict[str, float] = {}
    sources = list(SOURCES) if req.sources is None else req.sources
    tasks: dict[Source, asyncio.Task[list[Item]]] = {}
    if any(s in lanes for s in sources):
        embedding = asyncio.create_task(embed(req.query))
        embedding.add_done_callback(lambda _: stages.setdefault("embed", _ms(loop, began)))
        tasks = {
            s: asyncio.create_task(_run(lanes[s], req.query, embedding))
            for s in sources
            if s in lanes
        }
        await asyncio.wait(tasks.values(), timeout=deadline)
        embedding.cancel()  # a no-op once finished; stops one the deadline cut off
        stages["lanes"] = _ms(loop, began)

    outcome: dict[Source, str] = {}
    contributions: list[tuple[int, int, Item]] = []
    for order, s in enumerate(sources):
        task = tasks.get(s)
        if task is None:
            outcome[s] = "skipped"
        elif not task.done():
            task.cancel()
            outcome[s] = "timed_out"
        elif task.exception() is not None:
            outcome[s] = "failed"
        else:
            outcome[s] = "ok"
            contributions += [(item.matched_by[0].rank, order, item) for item in task.result()]

    items = _merge(contributions)[: req.k or K_DEFAULT]
    lanes_out = {
        s: Lane(
            outcome=outcome[s],
            candidates=len({i.uri for _, o, i in contributions if sources[o] == s}),
            returned=sum(any(m.lane == s for m in i.matched_by) for i in items),
        )
        for s in sources
    }
    stages["total"] = _ms(loop, began)
    return SearchResponse(
        status=_status(lanes_out, items),
        lanes=lanes_out,
        items=items,
        trace=Trace(stages_ms=stages, code_rev=os.environ.get("CODE_REV")),
    )


def _ms(loop: asyncio.AbstractEventLoop, since: float) -> float:
    return round((loop.time() - since) * 1000, 1)


def _merge(contributions: list[tuple[int, int, Item]]) -> list[Item]:
    """One item per uri, placed at its best contribution's rank (ties by lane
    order); `matched_by` keeps every contribution, `passages` their de-duplicated
    union, best first."""
    merged: dict[str, Item] = {}
    for _, _, item in sorted(contributions, key=lambda c: (c[0], c[1])):
        if item.uri not in merged:
            merged[item.uri] = item.model_copy(update={"matched_by": [], "passages": []})
        into = merged[item.uri]
        into.matched_by += item.matched_by
        into.passages += [p for p in item.passages if p not in into.passages]
    return list(merged.values())
