"""One search, run as the hub contract `contracts/search.md` §3 defines it: lanes
run concurrently, each reports an outcome, and the status follows from them."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import NamedTuple

from searcher.models import (
    SOURCES,
    Item,
    Lane,
    Outcome,
    SearchRequest,
    SearchResponse,
    Source,
    Status,
    Trace,
)

log = logging.getLogger(__name__)

# A lane gets the query, its embedding and the deadline as an event-loop time
# (None: no deadline), and returns its pieces best first, each carrying exactly
# one `matched_by` entry, its own. Lanes must be async all the way down (no
# `to_thread`): the deadline cancels them, and cancellation cannot stop a thread.
LaneFn = Callable[[str, list[float], float | None], Awaitable[list[Item]]]
Embedder = Callable[[str], Awaitable[list[float]]]

# ponytail: one default until per-caller defaults are set from today's callers.
K_DEFAULT = 10
CODE_REV = os.environ.get("CODE_REV")


class Contribution(NamedTuple):
    rank: int
    order: int  # the lane's position in the request's `sources`
    source: Source
    item: Item


async def search(
    req: SearchRequest,
    lanes: Mapping[Source, LaneFn],
    embed: Embedder | None,
    deadline: float | None,
) -> SearchResponse:
    """Answer one request. `deadline` is seconds for the whole search, embedding
    included; past it, unfinished lanes are `timed_out`. None waits for every lane.
    `embed` is only called when a requested lane is wired."""
    loop = asyncio.get_running_loop()
    began = loop.time()
    deadline_at = None if deadline is None else began + deadline
    stages: dict[str, float] = {}
    sources = list(SOURCES) if req.sources is None else req.sources
    tasks: dict[Source, asyncio.Task[list[Item]]] = {}
    wired = [s for s in sources if s in lanes]
    if wired and embed is not None:
        embedding = asyncio.create_task(embed(req.query))
        embedding.add_done_callback(lambda _: stages.setdefault("embed", _ms(loop, began)))

        async def lane_task(lane: LaneFn) -> list[Item]:
            # Shielded: one lane timing out must not cancel the embedding the others share.
            return await lane(req.query, await asyncio.shield(embedding), deadline_at)

        tasks = {s: asyncio.create_task(lane_task(lanes[s])) for s in wired}
        await asyncio.wait(tasks.values(), timeout=deadline)
        late = [t for t in (embedding, *tasks.values()) if not t.done()]
        for t in late:
            t.cancel()
        await asyncio.gather(*late, return_exceptions=True)
        stages["lanes"] = round(_ms(loop, began) - stages.get("embed", 0.0), 1)

    merge_began = loop.time()
    outcome: dict[Source, Outcome] = {}
    contributions: list[Contribution] = []
    for order, source in enumerate(sources):
        task = tasks.get(source)
        if task is None:
            outcome[source] = "skipped"
        elif task.cancelled():
            outcome[source] = "timed_out"
        elif (exc := task.exception()) is not None:
            log.warning("lane %s failed", source, exc_info=exc)
            outcome[source] = "failed"
        else:
            outcome[source] = "ok"
            contributions += [
                Contribution(item.matched_by[0].rank, order, source, item) for item in task.result()
            ]

    items = _merge(contributions)[: req.k or K_DEFAULT]
    lanes_out = {
        source: Lane(
            outcome=outcome[source],
            candidates=len({c.item.uri for c in contributions if c.source == source}),
            returned=sum(any(m.lane == source for m in i.matched_by) for i in items),
        )
        for source in sources
    }
    stages["merge"] = _ms(loop, merge_began)
    stages["total"] = _ms(loop, began)
    return SearchResponse(
        status=_status(lanes_out, items),
        lanes=lanes_out,
        items=items,
        trace=Trace(stages_ms=stages, code_rev=CODE_REV),
    )


def _status(lanes: dict[Source, Lane], items: list[Item]) -> Status:
    if not any(lane.outcome == "ok" for lane in lanes.values()):
        return "unavailable"
    if any(lane.outcome != "ok" for lane in lanes.values()):
        return "partial"
    return "ok" if items else "nothing_matched"


def _ms(loop: asyncio.AbstractEventLoop, since: float) -> float:
    return round((loop.time() - since) * 1000, 1)


def _merge(contributions: list[Contribution]) -> list[Item]:
    """One item per uri, placed at its best contribution's rank (ties by lane
    order); `matched_by` keeps every contribution, `passages` their de-duplicated
    union, best first."""
    merged: dict[str, Item] = {}
    for c in sorted(contributions, key=lambda c: (c.rank, c.order)):
        if c.item.uri not in merged:
            merged[c.item.uri] = c.item.model_copy(update={"matched_by": [], "passages": []})
        target = merged[c.item.uri]
        target.matched_by += c.item.matched_by
        target.passages += [p for p in c.item.passages if p not in target.passages]
    return list(merged.values())
