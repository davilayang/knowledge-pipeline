"""FastAPI app factory for the search service."""

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chromadb
from fastapi import FastAPI
from retrievers.embedding import AsyncQueryEmbedder

from searcher.corpus import corpus_lane
from searcher.engine import NO_DEADLINE_S, Embedder, LaneFn, search, time_limit
from searcher.models import SearchRequest, SearchResponse, Source

# Voice must answer inside its budget; MCP waits for every lane.
DEADLINE = {"voice": 5.0, "mcp": None}


def create_app(
    lanes: Mapping[Source, LaneFn] | None = None, embed: Embedder | None = None
) -> FastAPI:
    """With no lanes wired, every requested lane is skipped and, by the contract's
    status precedence, the answer is `unavailable`."""
    app = FastAPI(title="kp-searcher")
    wired = lanes or {}

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/search")
    async def search_endpoint(req: SearchRequest) -> SearchResponse:
        return await search(req, wired, embed, DEADLINE[req.caller])

    return app


_chroma: Any = None
_embedder = AsyncQueryEmbedder()


async def _chroma_client() -> Any:
    """One Chroma client per process, built on first use inside the caller's time limit."""
    global _chroma
    if _chroma is None:
        _chroma = await chromadb.AsyncHttpClient(
            host=os.environ.get("CHROMA_HOST", "chroma"),
            port=int(os.environ.get("CHROMA_PORT", "8000")),
        )
    return _chroma


async def _embed(text: str, deadline_at: float | None) -> list[float]:
    async with time_limit(deadline_at):
        left = (
            NO_DEADLINE_S
            if deadline_at is None
            else deadline_at - asyncio.get_running_loop().time()
        )
        return await _embedder.embed(text, timeout=max(left, 0.001))


# newsletter-assistant's data directory, mounted read-write (see corpus._resolve).
NA_DATA = Path(os.environ.get("NA_DATA_DIR", "/na-data"))

app = create_app(lanes={"corpus": corpus_lane(_chroma_client, NA_DATA / "corpus.db")}, embed=_embed)
