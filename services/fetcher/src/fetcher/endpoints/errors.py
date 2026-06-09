"""FastAPI exception handlers for FetcherError."""

from fastapi import Request
from fastapi.responses import JSONResponse

from fetcher.errors import FetcherError
from fetcher.problems import problem_body


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    retryable: bool,
    retry_after_seconds: int | None = None,
    canonical_url: str | None = None,
    tier_log: list | None = None,
) -> JSONResponse:
    """Create a JSONResponse with application/problem+json media type."""
    body = problem_body(
        status=status,
        code=code,
        title=title,
        detail=detail,
        instance=instance,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        canonical_url=canonical_url,
        tier_log=tier_log,
    )
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


async def fetcher_exception_handler(request: Request, exc: FetcherError) -> JSONResponse:
    """FastAPI handler for FetcherError and its subclasses."""
    return problem_response(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        instance=str(request.url.path),
        retryable=exc.retryable,
        retry_after_seconds=exc.retry_after_seconds,
        canonical_url=exc.extra.get("canonical_url"),
        tier_log=exc.extra.get("tier_log"),
    )
