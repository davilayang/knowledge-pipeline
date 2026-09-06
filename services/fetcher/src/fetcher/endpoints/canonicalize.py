"""GET /v1/canonicalize with url_aliases caching."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from fetcher.cache import canonicalize_cached


router = APIRouter(tags=["Utilities"])


@router.get(
    "/v1/canonicalize",
    summary="Resolve a URL through redirects and strip tracking params; cached.",
)
async def canonicalize_endpoint(
    request: Request,
    url: str = Query(...),
    force_refresh: bool = Query(default=False),
) -> Any:
    settings = request.app.state.settings
    result, cache_hit = canonicalize_cached(
        url,
        db_path=Path(settings.db_path),
        ttl_days=settings.canonicalize_ttl_days,
        force_refresh=force_refresh,
    )
    return {
        "input_url": result.input_url,
        "canonical_url": result.canonical_url,
        "redirects_followed": result.redirects_followed,
        "params_stripped": result.params_stripped,
        "cache_hit": cache_hit,
    }
