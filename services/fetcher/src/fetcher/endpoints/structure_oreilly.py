"""POST /v1/structure-oreilly: a saved O'Reilly reader page -> markdown.

Deterministic, unlike its sibling routes — no model, no network call, no cascade.
That decides the cache key too: `/v1/structure` keys on prompt and chain shas,
which name nothing here, so this route keys on the page and the converter version.

The publisher answers an automated fetch with an access-denied redirect, so the
page can only arrive as a payload a human captured.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fetcher.cache import lookup as cache_lookup, upsert as cache_upsert
from fetcher.endpoints.errors import problem_response
from fetcher.endpoints.schemas import ProblemResponse
from fetcher.extractors.oreilly_htmlbook import (
    CONVERTER_VERSION,
    ConversionRejected,
    convert_page,
)


router = APIRouter(tags=["Normalize"])

_ENDPOINT_KEY = "structure-oreilly"
_KIND = "book-chapter"
_TIER = "oreilly-htmlbook"


class StructureOreillyRequest(BaseModel):
    page_html: str = ""
    source_url: str | None = None


def _cache_key(page_html: str) -> str:
    digest = hashlib.sha256(page_html.encode("utf-8")).hexdigest()
    return f"{_ENDPOINT_KEY}:v{CONVERTER_VERSION}:{digest}"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _body(
    *, markdown: str, metadata: dict[str, Any], fetched_at: str, cache_hit: bool
) -> dict[str, Any]:
    return {
        "markdown": markdown,
        "kind": _KIND,
        "tier_used": _TIER,
        "fetched_at": fetched_at,
        "cache_hit": cache_hit,
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", []),
        "figure_anchors": metadata.get("figure_anchors", []),
        "figure_captions": metadata.get("figure_captions", []),
        "metadata": metadata,
    }


@router.post(
    "/v1/structure-oreilly",
    summary="Convert a saved O'Reilly reader chapter page into markdown.",
    responses={
        400: {"model": ProblemResponse, "description": "Empty `page_html`."},
        422: {
            "model": ProblemResponse,
            "description": "Not a chapter page, or markup the converter will not vouch for.",
        },
    },
)
async def structure_oreilly(req: StructureOreillyRequest, request: Request) -> Any:
    if not req.page_html.strip():
        return problem_response(
            status=400,
            code="BAD_REQUEST",
            title="Empty page_html",
            detail="page_html must be non-empty",
            instance=str(request.url.path),
            retryable=False,
        )

    settings = request.app.state.settings
    db_path = Path(settings.db_path)
    cache_key = _cache_key(req.page_html)

    cached = cache_lookup(db_path=db_path, canonical_url=cache_key)
    if cached is not None:
        return JSONResponse(
            content=_body(
                markdown=cached.markdown,
                metadata=dict(cached.metadata or {}),
                fetched_at=cached.fetched_at,
                cache_hit=True,
            )
        )

    try:
        conversion = convert_page(req.page_html)
    except ConversionRejected as exc:
        # Refusing is the point: a silently flattened table or a dropped
        # paragraph would reach the corpus as the author's own words.
        code = "NOT_A_CHAPTER" if "data-type='chapter'" in str(exc) else "CONVERSION_REJECTED"
        return problem_response(
            status=422,
            code=code,
            title="Page could not be converted faithfully",
            detail=str(exc),
            instance=str(request.url.path),
            retryable=False,
        )

    metadata: dict[str, Any] = {
        "title": conversion.title,
        "authors": conversion.authors,
        "figure_anchors": conversion.figure_anchors,
        "figure_captions": conversion.figure_captions,
        "converter_version": CONVERTER_VERSION,
    }
    fetched_at = _iso_now()
    cache_upsert(
        db_path=db_path,
        canonical_url=cache_key,
        source_type=_KIND,
        markdown=conversion.markdown,
        tier_used=_TIER,
        metadata=metadata,
        tier_log=[],
        ttl_days=settings.cache_ttl_days,
    )
    return JSONResponse(
        content=_body(
            markdown=conversion.markdown,
            metadata=metadata,
            fetched_at=fetched_at,
            cache_hit=False,
        )
    )
