"""POST /v1/fetch: synchronous single URL to markdown."""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from fetcher.endpoints.schemas import ProblemResponse
from fetcher.errors import BadUrl
from fetcher.fetch_service import run_fetch_request
from fetcher.types import FetchRequest, TierLogEntry


router = APIRouter(tags=["Fetch"])


def _validate_url_shape(url: str) -> None:
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise BadUrl(f"malformed URL: {url}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadUrl(f"malformed URL: {url}")


def _tier_log_payload(tier_log: list[TierLogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "tier": entry.tier,
            "status": entry.status,
            "chars": entry.chars,
            "error": entry.error,
            "validated": entry.validated,
            "duration_ms": entry.duration_ms,
            "floor": entry.floor,
            "error_kind": entry.error_kind,
            "detail": entry.detail,
        }
        for entry in tier_log
    ]


@router.post(
    "/v1/fetch",
    summary="Synchronously fetch a URL → markdown via per-source handler cascade.",
    responses={
        400: {"model": ProblemResponse, "description": "Malformed URL (`BadUrl`)."},
        422: {
            "model": ProblemResponse,
            "description": "No handler matches the URL (`UnsupportedKind`).",
        },
        429: {
            "model": ProblemResponse,
            "description": "Per-key semaphore exhausted (`RateLimited`).",
        },
        502: {"model": ProblemResponse, "description": "All tiers failed (`UpstreamFailure`)."},
        504: {
            "model": ProblemResponse,
            "description": "Per-request deadline exceeded (`UpstreamTimeout`).",
        },
    },
)
async def fetch(
    req: FetchRequest,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Any:
    _validate_url_shape(req.url)

    settings = request.app.state.settings
    outcome = await run_fetch_request(
        req,
        db_path=Path(settings.db_path),
        ctx=request.app.state.fetch_context,
        ttl_days=settings.cache_ttl_days,
    )

    if if_none_match and if_none_match.strip('"') == outcome.etag:
        return Response(
            status_code=304,
            headers={
                "etag": f'"{outcome.etag}"',
                "last-modified": outcome.fetched_at,
            },
        )

    body = {
        "markdown": outcome.markdown,
        "kind": outcome.kind,
        "canonical_url": outcome.canonical_url,
        "tier_used": outcome.tier_used,
        "fetched_at": outcome.fetched_at,
        "cache_hit": outcome.cache_hit,
        "etag": outcome.etag,
        "tier_log": _tier_log_payload(outcome.tier_log),
        "metadata": outcome.metadata or {},
    }

    return JSONResponse(
        content=body,
        headers={
            "etag": f'"{outcome.etag}"',
            "last-modified": outcome.fetched_at,
            "cache-control": f"private, max-age={settings.cache_ttl_days * 86400}",
        },
    )
