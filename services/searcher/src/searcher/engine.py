"""One search, run as the hub contract `contracts/search.md` §3 defines it: lanes
run concurrently, each reports an outcome, and the status follows from them."""

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NamedTuple

from searcher.models import (
    SOURCES,
    Item,
    Lane,
    MatchedBy,
    Outcome,
    SearchRequest,
    SearchResponse,
    Source,
    Status,
    Trace,
)

log = logging.getLogger(__name__)

# A lane gets the request, the query's embedding and the deadline as an
# event-loop time (None: no deadline), and returns its pieces best first, each
# carrying exactly one `matched_by` entry, its own. Lanes must be async all the
# way down (no `to_thread`): the deadline cancels them, and cancellation cannot
# stop a thread.
LaneFn = Callable[[SearchRequest, list[float], float | None], Awaitable[list[Item]]]
# An embedder gets the query and the same deadline as the lanes.
EmbedFn = Callable[[str, float | None], Awaitable[list[float]]]

# ponytail: one default until per-caller defaults are set from today's callers.
K_DEFAULT = 10
CODE_REV = os.environ.get("CODE_REV")
CLEANUP_S = 0.1
# An MCP search has no deadline; each network call in it still gets a finite one.
NO_DEADLINE_S = 30.0


# The running search's `trace.stages_ms`; lane tasks inherit it, so a lane's
# blocking read lands in the trace of the search that ran it.
_STAGES: ContextVar[dict[str, float] | None] = ContextVar("stages", default=None)


@contextmanager
def timed(stage: str) -> Iterator[None]:
    """Record how long the block takes, in ms, as `stage` in the search's trace."""
    began = time.perf_counter()
    try:
        yield
    finally:
        if (stages := _STAGES.get()) is not None:
            stages[stage] = round((time.perf_counter() - began) * 1000, 1)


class DeadlineExceeded(TimeoutError):
    """The search's own deadline ran out. Only this is `timed_out`; any other
    TimeoutError, such as a client read or the no-deadline cap, is a failure."""


@asynccontextmanager
async def time_limit(deadline_at: float | None) -> AsyncIterator[None]:
    """The search deadline as an elapsed-time limit for one lane's or the
    embedder's network calls, or `NO_DEADLINE_S` when the search has none.
    The deadline's own expiry raises `DeadlineExceeded`; the cap's stays a plain
    TimeoutError."""
    if deadline_at is None:
        async with asyncio.timeout(NO_DEADLINE_S):
            yield
        return
    limit = asyncio.timeout_at(deadline_at)
    try:
        async with limit:
            yield
    except TimeoutError as exc:
        if limit.expired():
            raise DeadlineExceeded("search deadline reached") from exc
        raise


class Hit(NamedTuple):
    """One chunk a method found, best first in its list."""

    key: str  # the piece's id in its lane; empty when the chunk names no piece
    chunk: str
    meta: dict[str, Any]
    score: float


# A method gets what a lane gets and returns its hits best first.
HitsFn = Callable[[SearchRequest, list[float], float | None], Awaitable[list[Hit]]]
# A lane's source lookup: piece id to whatever its builder needs, for the ids every
# method found, read once; an id left out was not found and its hits are dropped.
ResolveFn = Callable[[list[str]], Mapping[str, Any]]
# One item from a piece's first hit, with no `matched_by` yet; None drops the hit.
BuildFn = Callable[[SearchRequest, str, Any, Hit], Item | None]


def dense(
    chroma: Callable[[], Awaitable[Any]], collection_name: str, key: Callable[[dict[str, Any]], str]
) -> HitsFn:
    """The dense method over one Chroma collection; `key` reads a chunk's piece id
    from its metadata."""

    async def hits(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Hit]:
        async with time_limit(deadline_at):
            client = await chroma()
            # get_collection, never get_or_create: a search creates nothing.
            collection = await client.get_collection(collection_name, embedding_function=None)
            res = await collection.query(
                query_embeddings=[embedding],
                n_results=req.k or K_DEFAULT,
                include=["documents", "metadatas", "distances"],
            )
        return [
            Hit(key(meta), chunk, meta, 1 - dist)
            for chunk, meta, dist in zip(
                res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True
            )
        ]

    return hits


def lane(
    source: Source, methods: Mapping[str, HitsFn], resolve: ResolveFn, build: BuildFn
) -> LaneFn:
    """A lane: each method's hits, the source looked up once for all of them, then
    one item per piece per method. A piece's first eligible hit builds it and gives
    its score; its later hits only add their chunk to its passages. Ranks are
    1-based per method, counted after dropped hits, so they have no gaps. The lane
    is all or nothing: a method that fails or times out fails or times out the lane.

    ponytail: methods run one after another and contributions only meet in the
    engine's best-rank merge. Running methods concurrently, fusing them within the
    lane (e.g. RRF), and a keyword method that needs no embedding wait until a
    second method exists.
    """

    async def run(
        req: SearchRequest, embedding: list[float], deadline_at: float | None
    ) -> list[Item]:
        found = {
            m: [h for h in await fn(req, embedding, deadline_at) if h.key]
            for m, fn in methods.items()
        }
        resolved = resolve(sorted({h.key for hs in found.values() for h in hs}))
        out: list[Item] = []
        for method, hs in found.items():
            items: dict[str, Item] = {}
            for h in hs:
                if h.key in items:
                    if h.chunk not in items[h.key].passages:
                        items[h.key].passages.append(h.chunk)
                    continue
                if h.key not in resolved or (item := build(req, h.key, resolved[h.key], h)) is None:
                    continue
                item.matched_by = [
                    MatchedBy(
                        query=req.query,
                        lane=source,
                        method=method,
                        rank=len(items) + 1,
                        score=h.score,
                    )
                ]
                items[h.key] = item
            out += items.values()
        return out

    return run


def open_ro(db_path: Path) -> sqlite3.Connection:
    """A read-only connection to a database newsletter-assistant keeps writing.

    - `mode=ro`, never `immutable`, which would stop SQLite noticing those writes.
    - A WAL database closed cleanly has no `-shm` file, and SQLite recreates it on
      open, so the directory must be writable by this process.
    - `timeout=0`: lanes run one batched primary-key query, with no busy wait.
    """
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0)
    conn.row_factory = sqlite3.Row
    return conn


class Contribution(NamedTuple):
    rank: int
    order: int  # the lane's position in the request's `sources`
    source: Source
    item: Item


async def search(
    req: SearchRequest,
    lanes: Mapping[Source, LaneFn],
    embed: EmbedFn | None,
    deadline: float | None,
) -> SearchResponse:
    """Answer one request. `deadline` is seconds for the whole search, embedding
    included; past it, unfinished lanes are `timed_out`. None waits for every lane.
    `embed` is only called when a requested lane is wired."""
    stages: dict[str, float] = {}
    token = _STAGES.set(stages)
    try:
        return await _search(req, lanes, embed, deadline, stages)
    finally:
        _STAGES.reset(token)


async def _search(
    req: SearchRequest,
    lanes: Mapping[Source, LaneFn],
    embed: EmbedFn | None,
    deadline: float | None,
    stages: dict[str, float],
) -> SearchResponse:
    loop = asyncio.get_running_loop()
    began = loop.time()
    deadline_at = None if deadline is None else began + deadline
    sources = list(SOURCES) if req.sources is None else req.sources
    tasks: dict[Source, asyncio.Task[list[Item]]] = {}
    wired = [s for s in sources if s in lanes]
    if wired and embed is not None:
        embedding = asyncio.create_task(embed(req.query, deadline_at))
        embedding.add_done_callback(lambda _: stages.setdefault("embed", _ms(loop, began)))

        async def lane_task(lane: LaneFn) -> list[Item]:
            # Shielded: one lane timing out must not cancel the embedding the others share.
            return await lane(req, await asyncio.shield(embedding), deadline_at)

        tasks = {s: asyncio.create_task(lane_task(lanes[s])) for s in wired}
        try:
            await asyncio.wait(tasks.values(), timeout=deadline)
        finally:
            # Also runs when the search itself is cancelled, so no lane outlives it.
            late = [t for t in (embedding, *tasks.values()) if not t.done()]
            for t in late:
                t.cancel()
            if late:
                # Bounded: a lane slow to unwind must not hold the answer past the deadline.
                await asyncio.wait(late, timeout=CLEANUP_S)
        stages["lanes"] = round(_ms(loop, began) - stages.get("embed", 0.0), 1)

    merge_began = loop.time()
    outcome: dict[Source, Outcome] = {}
    contributions: list[Contribution] = []
    for order, source in enumerate(sources):
        task = tasks.get(source)
        if task is None:
            outcome[source] = "skipped"
        elif not task.done() or task.cancelled():
            outcome[source] = "timed_out"
        elif isinstance(task.exception(), DeadlineExceeded):
            # The lane's own `time_limit` fires at the deadline, a moment before
            # the wait above gives up on it: still a timeout (contract §3).
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
