"""FastAPI app factory, /healthz, and fetcher endpoints."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from domains.fetch_store.sources import create_schema, mark_orphans_failed
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from fetcher.config import Settings
from fetcher.context import make_fetch_context
from fetcher.endpoints import canonicalize as canonicalize_endpoint
from fetcher.endpoints import fetch as fetch_endpoint
from fetcher.endpoints import fetches as fetches_endpoint
from fetcher.endpoints import structure as structure_endpoint
from fetcher.endpoints import structure_transcript as structure_transcript_endpoint
from fetcher.endpoints.errors import fetcher_exception_handler
from fetcher.errors import FetcherError
from fetcher.registry import REGISTERED_HANDLERS

logger = logging.getLogger(__name__)

_registered_kinds: list[str] = []


_APP_DESCRIPTION = """\
Synchronous URL → markdown fetcher with a content-keyed cache.

Per-source handlers (article / arxiv / medium / pdf / youtube) run a
**cascade** of tiers; the first tier whose output meets the quality floor
wins. Cloud-LLM **structurers** clean noisy article bodies (`/v1/structure`)
and turn YouTube auto-captions into speaker-attributed paragraphs
(YouTube handler tier + `/v1/structure-transcript`).

This service is consumed by both `knowledge-pipeline` (Dagster
orchestrators) and `newsletter-assistant` (voice / MCP / web agents)
in the personal-knowledge-OS. Run with `uvicorn --workers 1` — the
SQLite cache layer relies on the single-worker invariant.
"""


_OPENAPI_TAGS = [
    {"name": "Health", "description": "Liveness + readiness probes."},
    {
        "name": "Fetch",
        "description": (
            "URL → markdown via per-source handler cascade. Sync (`/v1/fetch`) "
            "and async-job (`/v1/fetches`) shapes."
        ),
    },
    {
        "name": "Normalize",
        "description": (
            "Markdown cleanup + transcript structuring. Source-agnostic; "
            "reused by handlers and external callers."
        ),
    },
    {"name": "Utilities", "description": "Canonicalization, cache inspection."},
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize settings, DB schema, and shared fetch context on startup."""

    try:
        settings = Settings()
    except ValidationError:
        app.state.settings_ok = False
        app.state.settings = None
        app.state.fetch_context = None
        yield
        return

    db_path = Path(settings.db_path)

    # Initialise sqlite db
    create_schema(db_path=db_path)

    # Label pending fetches (prior to service restart) as failed
    json_restart_error = (
        '{"code":"SERVICE_RESTARTED","title":"Service restarted while job was running",'
        '"detail":"Issue a fresh POST /v1/fetches to retry.","retryable":true}'
    )
    n_swept = mark_orphans_failed(db_path=db_path, error_json=json_restart_error)
    if n_swept:
        logger.info("fetcher.boot: swept %d orphaned jobs", n_swept)

    app.state.settings = settings
    async with make_fetch_context(settings) as ctx:
        app.state.fetch_context = ctx
        _registered_kinds.clear()
        _registered_kinds.extend(handler.NAME for handler in REGISTERED_HANDLERS)
        app.state.settings_ok = True
        yield


def _missing_settings(exc: ValidationError) -> list[str]:
    missing: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        if loc:
            missing.append(f"FETCHER_{str(loc[0]).upper()}")
    return missing


def create_app() -> FastAPI:
    # force=True overrides uvicorn's pre-installed root logger config so
    # `fetcher.*` INFO lines aren't silently dropped.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    app = FastAPI(
        title="kp-fetcher",
        summary="Content acquisition + normalization service for the knowledge-pipeline OS.",
        description=_APP_DESCRIPTION,
        version="0.1.0",
        openapi_tags=_OPENAPI_TAGS,
        lifespan=_lifespan,
    )
    app.add_exception_handler(FetcherError, fetcher_exception_handler)
    app.include_router(fetch_endpoint.router)
    app.include_router(canonicalize_endpoint.router)
    app.include_router(fetches_endpoint.router)
    app.include_router(structure_endpoint.router)
    app.include_router(structure_transcript_endpoint.router)

    @app.get(
        "/healthz",
        tags=["Health"],
        summary="Liveness + readiness probe — reports config validity and registered handlers.",
    )
    async def healthz() -> Any:
        try:
            Settings()
        except ValidationError as exc:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "missing": _missing_settings(exc)},
            )

        return {"ok": True, "registered_kinds": list(_registered_kinds)}

    return app


app = create_app()
