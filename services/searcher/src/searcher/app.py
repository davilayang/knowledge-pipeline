"""FastAPI app factory for the search service."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chromadb
from fastapi import FastAPI
from retrievers.embedding import AsyncQueryEmbedder

from searcher.corpus import corpus_lane
from searcher.engine import EmbedFn, LaneFn, search, time_limit
from searcher.models import SearchRequest, SearchResponse, Source
from searcher.sessions import sessions_lane

# Voice must answer inside its budget; MCP waits for every lane.
DEADLINE = {"voice": 5.0, "mcp": None}


def create_app(
    lanes: Mapping[Source, LaneFn] | None = None, embed: EmbedFn | None = None
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


async def _embed(text: str, deadline_at: float | None) -> list[float]:
    global _embedder
    if _embedder is None:
        _embedder = AsyncQueryEmbedder()
    async with time_limit(deadline_at):
        return await _embedder.embed(text)


# newsletter-assistant's data directory, mounted read-write (see corpus._resolve).
NEWSLETTER_DATA = Path(os.environ.get("NEWSLETTER_DATA_DIR", "/newsletter-data"))

app = create_app(
    lanes={
        "corpus": corpus_lane(_chroma_client, NEWSLETTER_DATA / "corpus.db"),
        "sessions": sessions_lane(_chroma_client, NEWSLETTER_DATA / "sessions.db"),
    },
    embed=_embed,
)
