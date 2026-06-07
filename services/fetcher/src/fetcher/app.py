"""FastAPI app factory and /healthz endpoint.

Phase 0 scope: only /healthz exists. /v1/fetch and related endpoints arrive in Phase 1.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from fetcher.config import Settings
from fetcher.db import init_schema, open_connection


_registered_sources: list[str] = []


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize DB schema on startup. Source registry pre-warm lands in Phase 1."""
    try:
        settings = Settings()
        conn = open_connection(settings.db_path)
        try:
            init_schema(conn)
        finally:
            conn.close()
        app.state.settings_ok = True
    except ValidationError:
        app.state.settings_ok = False

    yield


def _missing_settings(exc: ValidationError) -> list[str]:
    missing: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        if loc:
            missing.append(f"FETCHER_{str(loc[0]).upper()}")
    return missing


def create_app() -> FastAPI:
    app = FastAPI(title="fetcher", version="0.1.0", lifespan=_lifespan)

    @app.get("/healthz")
    async def healthz() -> Any:
        try:
            Settings()
        except ValidationError as exc:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "missing": _missing_settings(exc)},
            )

        return {"ok": True, "registered_sources": list(_registered_sources)}

    return app


app = create_app()
