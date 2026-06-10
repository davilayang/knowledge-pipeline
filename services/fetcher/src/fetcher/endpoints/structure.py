"""POST /v1/structure: cloud LLM cascade over user-pasted article content."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fetcher.canonicalize import canonicalize
from fetcher.endpoints.errors import problem_response
from fetcher.extractors.structure import (
    StructurerCascadeFailed,
    run_cascade,
)
from fetcher.types import FetchResult, TierLogEntry


router = APIRouter()


_PROMPT_VERSION = "v1"


class StructureRequest(BaseModel):
    raw_content: str = ""
    source_url: str | None = None
    title: str | None = None
    content_date: str | None = None
    author_name: str | None = None


def _tier_log_payload(tier_log: list[TierLogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "tier": entry.tier,
            "status": entry.status,
            "chars": entry.chars,
            "error": entry.error,
            "validated": entry.validated,
        }
        for entry in tier_log
    ]


def _success_body(result: FetchResult) -> dict[str, Any]:
    metadata = dict(result.metadata or {})
    metadata.setdefault("prompt_version", _PROMPT_VERSION)
    return {
        "markdown": result.markdown,
        "kind": result.kind,
        "canonical_url": result.canonical_url,
        "tier_used": result.tier_used,
        "fetched_at": result.fetched_at,
        "cache_hit": result.cache_hit,
        "etag": result.etag,
        "tier_log": _tier_log_payload(result.tier_log),
        "metadata": metadata,
    }


@router.post("/v1/structure")
async def structure(req: StructureRequest, request: Request) -> Any:
    if not req.raw_content.strip():
        return problem_response(
            status=400,
            code="BAD_REQUEST",
            title="Empty raw_content",
            detail="raw_content must be non-empty",
            instance=str(request.url.path),
            retryable=False,
        )

    canonical_url = ""
    if req.source_url:
        canonical_url = canonicalize(req.source_url).canonical_url

    ctx = request.app.state.fetch_context

    try:
        result = await run_cascade(
            ctx,
            raw_content=req.raw_content,
            source_url=canonical_url,
            title=req.title,
            content_date=req.content_date,
            author_name=req.author_name,
            prompt="",
        )
    except StructurerCascadeFailed as exc:
        if not exc.retryable and "no api keys" in exc.last_error.lower():
            return problem_response(
                status=503,
                code="STRUCTURER_UNCONFIGURED",
                title="Structurer not configured",
                detail=exc.last_error,
                instance=str(request.url.path),
                retryable=False,
                tier_log=_tier_log_payload(exc.tier_log),
            )
        return problem_response(
            status=502,
            code="STRUCTURER_UPSTREAM_FAILURE",
            title="Structurer cascade exhausted",
            detail=exc.last_error,
            instance=str(request.url.path),
            retryable=exc.retryable,
            tier_log=_tier_log_payload(exc.tier_log),
        )

    return JSONResponse(content=_success_body(result))
