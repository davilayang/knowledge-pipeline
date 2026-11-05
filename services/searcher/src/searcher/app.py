"""FastAPI app factory for the search service."""

from fastapi import FastAPI

from searcher.engine import search
from searcher.models import SearchRequest, SearchResponse

# Voice must answer inside its budget; MCP waits for every lane.
DEADLINE = {"voice": 5.0, "mcp": None}


async def _no_embedder(text: str) -> list[float]:
    raise RuntimeError("no embedder is wired")


def create_app() -> FastAPI:
    app = FastAPI(title="kp-searcher")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/search")
    async def search_endpoint(req: SearchRequest) -> SearchResponse:
        # No lane is wired yet, so every requested lane is skipped and, by the
        # contract's status precedence, the answer is `unavailable`.
        return await search(req, {}, _no_embedder, DEADLINE[req.caller])

    return app


app = create_app()
