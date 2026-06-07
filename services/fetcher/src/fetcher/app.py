"""FastAPI app factory, /healthz, and fetcher endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from domains.fetches_store.sources import open_connection, create_schema
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from fetcher.config import Settings
from fetcher.context import make_fetch_context
from fetcher.endpoints import canonicalize as canonicalize_endpoint
from fetcher.endpoints import fetch as fetch_endpoint
from fetcher.endpoints import fetches as fetches_endpoint
from fetcher.errors import FetcherError, fetcher_exception_handler
from fetcher.registry import REGISTERED_SOURCES


_registered_sources: list[str] = []


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize settings, DB schema, and shared fetch context on startup."""
    try:
        settings = Settings()
        create_schema(settings.db_path)
        app.state.settings_ok = True
    except ValidationError:
        app.state.settings_ok = False
        app.state.settings = None
        app.state.fetch_context = None
        yield
        return

    app.state.settings = settings
    conn = open_connection(settings.db_path)
    # FIXME: why do this?
    try:
        init_schema(conn)
        conn.execute(
            """
            UPDATE fetches
               SET status = 'failed',
                   error_json = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE status IN ('pending', 'running')
            """,
            (
                json_restart_error(),
            ),
        )
    finally:
        conn.close()

    async with make_fetch_context(settings) as ctx:
        app.state.fetch_context = ctx
        _registered_sources.clear()
        _registered_sources.extend(source.NAME for source in REGISTERED_SOURCES)
        app.state.settings_ok = True
        yield


def json_restart_error() -> str:
    return (
        '{"code":"SERVICE_RESTARTED","title":"Service restarted while job was running",'
        '"detail":"Issue a fresh POST /v1/fetches to retry.","retryable":true}'
    )


def _missing_settings(exc: ValidationError) -> list[str]:
    missing: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        if loc:
            missing.append(f"FETCHER_{str(loc[0]).upper()}")
    return missing


def create_app() -> FastAPI:
    app = FastAPI(title="fetcher", version="0.1.0", lifespan=_lifespan)
    app.add_exception_handler(FetcherError, fetcher_exception_handler)
    app.include_router(fetch_endpoint.router)
    app.include_router(canonicalize_endpoint.router)
    app.include_router(fetches_endpoint.router)

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
