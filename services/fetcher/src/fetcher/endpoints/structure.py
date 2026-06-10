"""POST /v1/structure: cloud LLM cascade over user-pasted article content."""

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fetcher.cache import lookup as cache_lookup, upsert as cache_upsert
from fetcher.canonicalize import canonicalize
from fetcher.endpoints.errors import problem_response
from fetcher.extractors.structure import (
    StructurerCascadeFailed,
    run_cascade,
)
from fetcher.types import FetchResult, TierLogEntry


router = APIRouter()


_PROMPT_VERSION = "v1"


def _chain_head() -> tuple[str, str]:
    """Return the (provider, model) of the structurer chain's primary entry.

    Stubbed for SF3 cache-keying; SF4 replaces with a YAML-driven lookup.
    """
    return ("openai", "gpt-4.1-mini")


def _content_sha(raw_content: str) -> str:
    return hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def _structurer_cache_key(content_sha: str, provider: str, model: str) -> str:
    return f"structure:v1:{provider}:{model}:{_PROMPT_VERSION}:{content_sha}"


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

    settings = request.app.state.settings
    db_path = Path(settings.db_path)
    provider, model = _chain_head()
    cache_key = _structurer_cache_key(_content_sha(req.raw_content), provider, model)

    cached = cache_lookup(db_path=db_path, canonical_url=cache_key)
    if cached is not None:
        metadata = dict(cached.metadata or {})
        metadata.setdefault("prompt_version", _PROMPT_VERSION)
        return JSONResponse(
            content=_success_body(
                FetchResult(
                    markdown=cached.markdown,
                    kind="structured",
                    canonical_url=canonical_url,
                    tier_used=cached.tier_used,
                    fetched_at=cached.fetched_at,
                    cache_hit=True,
                    etag="",
                    tier_log=cached.tier_log,
                    metadata=metadata,
                )
            )
        )

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

    if result.tier_used.startswith("structurer:"):
        cache_upsert(
            db_path=db_path,
            canonical_url=cache_key,
            source_type="structured",
            markdown=result.markdown,
            tier_used=result.tier_used,
            metadata={**(result.metadata or {}), "prompt_version": _PROMPT_VERSION},
            tier_log=result.tier_log,
            ttl_days=settings.cache_ttl_days,
        )

    return JSONResponse(content=_success_body(result))
