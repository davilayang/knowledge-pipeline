"""RFC 7807 Problem Details helpers."""

from dataclasses import asdict

from fastapi import Request
from fastapi.responses import JSONResponse

from fetcher.types import Problem


_PROBLEM_TYPE_BASE = "https://fetcher/errors"


def problem(
    *,
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
    body = asdict(
        Problem(
            type=f"{_PROBLEM_TYPE_BASE}/{code.lower().replace('_', '-')}",
            title=title,
            status=status,
            code=code,
            detail=detail,
            instance=instance,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            canonical_url=canonical_url,
            tier_log=tier_log or [],
        )
    )
    body = {key: value for key, value in body.items() if value is not None}
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


class FetcherError(Exception):
    status = 500
    code = "INTERNAL_ERROR"
    title = "Internal error"
    retryable = False

    def __init__(self, detail: str, *, retry_after_seconds: int | None = None, **kwargs):
        super().__init__(detail)
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds
        self.extra = kwargs


class BadUrl(FetcherError):
    status, code, title, retryable = 400, "BAD_URL", "Malformed URL", False


class UnsupportedSource(FetcherError):
    status, code, title, retryable = 422, "UNSUPPORTED_SOURCE", "No source matches this URL", False


class UpstreamFailure(FetcherError):
    status, code, title, retryable = 502, "UPSTREAM_FAILURE", "All tiers failed", True


class UpstreamTimeout(FetcherError):
    status, code, title, retryable = 504, "UPSTREAM_TIMEOUT", "Per-request deadline exceeded", True


class RateLimited(FetcherError):
    status, code, title, retryable = 429, "RATE_LIMITED", "Per-source semaphore exhausted", True


async def fetcher_exception_handler(request: Request, exc: FetcherError) -> JSONResponse:
    return problem(
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
