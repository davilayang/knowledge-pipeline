"""POST /v1/structure: cloud LLM cascade over user-pasted article content."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fetcher.cache import lookup as cache_lookup, upsert as cache_upsert
from fetcher.canonicalize import canonicalize
from fetcher.endpoints.errors import problem_response
from fetcher.extractors import structure as _structure_extractor
from fetcher.extractors._cloud_chain import (
    cache_key_components,
    chain_config_sha,
    content_sha,
    prompt_sha,
)
from fetcher.extractors.structure import (
    StructurerCascadeFailed,
    run_cascade,
)
from fetcher.types import FetchResult, TierLogEntry


router = APIRouter(tags=["Normalize"])


_ENDPOINT_KEY = "structure"


class StructureRequest(BaseModel):
    raw_content: str = ""
    source_url: str | None = None
    title: str | None = None
    content_date: str | None = None
    author_name: str | None = None


def _structurer_cache_key(req: StructureRequest) -> str:
    """Compose the cache key covering every input that affects LLM output.

    Includes content, prompt-file contents, chain config (provider/model/timeout
    per entry), and hint context (title/author/content_date) — all of which
    feed into the user-message the chain sees. Today's pre-fix key omitted the
    last three categories, producing stale hits when any of them changed.
    """
    hint_blob = f"{req.title or ''}|{req.author_name or ''}|{req.content_date or ''}"
    content_only_sha = content_sha(req.raw_content)
    content_with_hints_sha = content_sha(f"{hint_blob}\n{content_only_sha}")
    return cache_key_components(
        endpoint=_ENDPOINT_KEY,
        content_sha_value=content_with_hints_sha,
        prompt_sha_value=prompt_sha(_structure_extractor.get_prompt()),
        chain_config_sha_value=chain_config_sha(_structure_extractor.get_chain()),
    )


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
    return {
        "markdown": result.markdown,
        "kind": result.kind,
        "canonical_url": result.canonical_url,
        "tier_used": result.tier_used,
        "fetched_at": result.fetched_at,
        "cache_hit": result.cache_hit,
        "etag": result.etag,
        "tier_log": _tier_log_payload(result.tier_log),
        "metadata": dict(result.metadata or {}),
    }


@router.post(
    "/v1/structure",
    summary="Clean a noisy article body into structured markdown via cloud LLM cascade.",
)
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
    cache_key = _structurer_cache_key(req)

    cached = cache_lookup(db_path=db_path, canonical_url=cache_key)
    if cached is not None:
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
                    metadata=dict(cached.metadata or {}),
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
            prompt=_structure_extractor.get_prompt(),
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
            metadata=dict(result.metadata or {}),
            tier_log=result.tier_log,
            ttl_days=settings.cache_ttl_days,
        )

    return JSONResponse(content=_success_body(result))
