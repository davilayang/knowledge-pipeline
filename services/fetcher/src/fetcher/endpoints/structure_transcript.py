"""POST /v1/structure-transcript: cloud LLM structuring over a transcript blob.

Thin wrapper around `transcript_structurer.structure_transcript`. Differs from
`/v1/structure`: no cascade (single LLM call, no trafilatura/passthrough
stages), and surfaces structurer failures (502/503) to the caller rather than
falling back to raw input — eval harnesses and debug tools want failures
explicit.

Cache key shares the helpers from `_cloud_chain` introduced in Phase A, but is
namespaced by endpoint so identical text routed through both endpoints can't
collide.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fetcher.cache import lookup as cache_lookup, upsert as cache_upsert
from fetcher.endpoints.errors import problem_response
from fetcher.endpoints.schemas import ProblemResponse
from fetcher.extractors import transcript_structurer as _transcript_extractor
from fetcher.extractors._cloud_chain import (
    cache_key_components,
    chain_config_sha,
    content_sha,
    prompt_sha,
)
from fetcher.extractors.transcript_structurer import (
    StructurerChainFailed,
    structure_transcript,
)


router = APIRouter(tags=["Normalize"])


_ENDPOINT_KEY = "structure-transcript"
_KIND = "structured-transcript"


class StructureTranscriptRequest(BaseModel):
    raw_transcript: str = ""
    title: str | None = None
    author: str | None = None
    content_date: str | None = None
    source_url: str | None = None


def _structurer_cache_key(req: StructureTranscriptRequest) -> str:
    hint_blob = f"{req.title or ''}|{req.author or ''}|{req.content_date or ''}"
    content_only = content_sha(req.raw_transcript)
    content_with_hints = content_sha(f"{hint_blob}\n{content_only}")
    return cache_key_components(
        endpoint=_ENDPOINT_KEY,
        content_sha_value=content_with_hints,
        prompt_sha_value=prompt_sha(_transcript_extractor.get_prompt()),
        chain_config_sha_value=chain_config_sha(_transcript_extractor.get_chain()),
    )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _success_body(
    *,
    markdown: str,
    tier_used: str,
    fetched_at: str,
    cache_hit: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "markdown": markdown,
        "kind": _KIND,
        "tier_used": tier_used,
        "fetched_at": fetched_at,
        "cache_hit": cache_hit,
        "metadata": metadata,
    }


@router.post(
    "/v1/structure-transcript",
    summary="Structure a raw transcript blob into speaker-attributed paragraphs.",
    responses={
        400: {"model": ProblemResponse, "description": "Empty `raw_transcript`."},
        502: {"model": ProblemResponse, "description": "Structurer cascade exhausted."},
        503: {
            "model": ProblemResponse,
            "description": "No structurer API keys configured.",
        },
    },
)
async def structure_transcript_endpoint(req: StructureTranscriptRequest, request: Request) -> Any:
    if not req.raw_transcript.strip():
        return problem_response(
            status=400,
            code="BAD_REQUEST",
            title="Empty raw_transcript",
            detail="raw_transcript must be non-empty",
            instance=str(request.url.path),
            retryable=False,
        )

    settings = request.app.state.settings
    db_path = Path(settings.db_path)
    cache_key = _structurer_cache_key(req)

    cached = cache_lookup(db_path=db_path, canonical_url=cache_key)
    if cached is not None:
        return JSONResponse(
            content=_success_body(
                markdown=cached.markdown,
                tier_used=cached.tier_used,
                fetched_at=cached.fetched_at,
                cache_hit=True,
                metadata=dict(cached.metadata or {}),
            )
        )

    ctx = request.app.state.fetch_context

    try:
        structured, tier_name, usage = await structure_transcript(
            ctx,
            req.raw_transcript,
            title=req.title,
            author=req.author,
            content_date=req.content_date,
        )
    except StructurerChainFailed as exc:
        if not exc.retryable and "no api keys" in str(exc).lower():
            return problem_response(
                status=503,
                code="STRUCTURER_UNCONFIGURED",
                title="Structurer not configured",
                detail=str(exc),
                instance=str(request.url.path),
                retryable=False,
            )
        return problem_response(
            status=502,
            code="STRUCTURER_UPSTREAM_FAILURE",
            title="Structurer cascade exhausted",
            detail=str(exc),
            instance=str(request.url.path),
            retryable=exc.retryable,
        )

    metadata: dict[str, Any] = {"structurer_tier": tier_name, "structurer_usage": usage}
    fetched_at = _iso_now()

    cache_upsert(
        db_path=db_path,
        canonical_url=cache_key,
        source_type=_KIND,
        markdown=structured,
        tier_used=tier_name,
        metadata=metadata,
        tier_log=[],
        ttl_days=settings.cache_ttl_days,
    )

    return JSONResponse(
        content=_success_body(
            markdown=structured,
            tier_used=tier_name,
            fetched_at=fetched_at,
            cache_hit=False,
            metadata=metadata,
        )
    )
