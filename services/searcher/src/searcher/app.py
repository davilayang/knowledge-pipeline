"""FastAPI app factory for the search service."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chromadb
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from retrievers.embedding import AsyncQueryEmbedder

from searcher.briefs import briefs_lane
from searcher.corpus import corpus_lane
from searcher.engine import EmbedFn, LaneFn, search, time_limit
from searcher.models import SearchRequest, SearchResponse, Source
from searcher.notes import notes_lane
from searcher.sessions import sessions_lane
from searcher.wiki import wiki_lane

# A search's deadline when the request sends no `deadline_ms`.
DEADLINE_MS_DEFAULT = 5000

# The most `mcp` searches running at once; the next is refused, never queued, so
# a burst of MCP searches cannot starve voice. `voice` is not limited.
# ponytail: a per-process counter; several workers would each allow this many,
# so a shared limit needs a shared store if workers are added.
MCP_MAX_IN_FLIGHT = 2


def create_app(
    lanes: Mapping[Source, LaneFn] | None = None, embed: EmbedFn | None = None
) -> FastAPI:
    """With no lanes wired, every requested lane is skipped and, by the contract's
    status precedence, the answer is `unavailable`."""
    app = FastAPI(title="kp-searcher")
    wired = lanes or {}
    in_flight = {"mcp": 0}

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/search", response_model=SearchResponse)
    async def search_endpoint(req: SearchRequest) -> SearchResponse | JSONResponse:
        # Checked and taken with no await in between, on the one event loop, so
        # two requests cannot both take the last slot.
        if req.caller == "mcp":
            if in_flight["mcp"] >= MCP_MAX_IN_FLIGHT:
                return JSONResponse({"detail": "search is busy"}, status_code=429)
            in_flight["mcp"] += 1
        try:
            return await search(req, wired, embed, (req.deadline_ms or DEADLINE_MS_DEFAULT) / 1000)
        finally:
            if req.caller == "mcp":
                in_flight["mcp"] -= 1

    return app


# One Chroma client and one embedder per process, each built on first use, so
# importing the app needs no key or connection.
_chroma: Any = None
_embedder: AsyncQueryEmbedder | None = None


async def _chroma_client() -> Any:
    global _chroma
    if _chroma is None:
        _chroma = await chromadb.AsyncHttpClient(
            host=os.environ.get("CHROMA_HOST", "chroma"),
            port=int(os.environ.get("CHROMA_PORT", "8000")),
        )
    return _chroma


async def _embed(text: str, deadline_at: float) -> list[float]:
    global _embedder
    if _embedder is None:
        _embedder = AsyncQueryEmbedder()
    async with time_limit(deadline_at):
        return await _embedder.embed(text)


# newsletter-assistant's data directory, mounted read-write (see engine.open_ro).
NEWSLETTER_DATA = Path(os.environ.get("NEWSLETTER_DATA_DIR", "/newsletter-data"))

# kp's own wiki directory, mounted read-only.
WIKI_DIR = Path(os.environ.get("WIKI_DIR", "/wiki"))

app = create_app(
    lanes={
        "corpus": corpus_lane(_chroma_client, NEWSLETTER_DATA / "corpus.db"),
        "sessions": sessions_lane(_chroma_client, NEWSLETTER_DATA / "sessions.db"),
        "notes": notes_lane(_chroma_client, NEWSLETTER_DATA / "notes"),
        "briefs": briefs_lane(_chroma_client, NEWSLETTER_DATA / "briefs"),
        "wiki": wiki_lane(_chroma_client, WIKI_DIR),
    },
    embed=_embed,
)
