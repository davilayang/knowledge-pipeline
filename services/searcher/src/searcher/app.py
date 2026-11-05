"""FastAPI app factory for the search service."""

from collections.abc import Mapping

from fastapi import FastAPI

from searcher.engine import Embedder, LaneFn, search
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


app = create_app()
