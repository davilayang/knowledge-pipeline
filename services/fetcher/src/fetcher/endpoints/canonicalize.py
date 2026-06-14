"""GET /v1/canonicalize with url_aliases caching."""

import hashlib
import json
from pathlib import Path
from typing import Any

from domains.fetches_store.sources import canonicalize_lookup, canonicalize_upsert
from fastapi import APIRouter, Query, Request

from fetcher.canonicalize import canonicalize


router = APIRouter(tags=["Utilities"])


def _input_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


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
    db_path = Path(settings.db_path)
    key = _input_hash(url)

    if not force_refresh:
        cached = canonicalize_lookup(db_path=db_path, input_url_hash=key)
        if cached is not None:
            return {
                "input_url": cached["input_url"],
                "canonical_url": cached["canonical_url"],
                "redirects_followed": json.loads(cached["redirects_json"]),
                "params_stripped": json.loads(cached["params_stripped"]),
                "cache_hit": True,
            }

    result = canonicalize(url)
    canonicalize_upsert(
        db_path=db_path,
        input_url_hash=key,
        input_url=result.input_url,
        canonical_url=result.canonical_url,
        redirects_json=json.dumps(result.redirects_followed),
        params_stripped=json.dumps(result.params_stripped),
        ttl_days=settings.cache_ttl_days,
    )
    return {
        "input_url": result.input_url,
        "canonical_url": result.canonical_url,
        "redirects_followed": result.redirects_followed,
        "params_stripped": result.params_stripped,
        "cache_hit": False,
    }
