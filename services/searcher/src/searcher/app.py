"""FastAPI app factory for the search service."""

from fastapi import FastAPI

from searcher.models import SOURCES, Lane, SearchRequest, SearchResponse


def create_app() -> FastAPI:
    app = FastAPI(title="kp-searcher")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/search")
    async def search(req: SearchRequest) -> SearchResponse:
        # No lane is wired yet, so every requested lane is skipped and, by the
        # contract's status precedence, the answer is `unavailable`.
        sources = list(SOURCES) if req.sources is None else req.sources
        return SearchResponse(
            status="unavailable",
            lanes={s: Lane(outcome="skipped", candidates=0, returned=0) for s in sources},
            items=[],
        )

    return app


app = create_app()
