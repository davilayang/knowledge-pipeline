"""Domain exceptions for the fetcher service."""


class FetcherError(Exception):
    """Base class for all fetcher errors."""

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


class UnsupportedKind(FetcherError):
    status, code, title, retryable = 422, "UNSUPPORTED_KIND", "No handler matches this URL", False


class UpstreamFailure(FetcherError):
    status, code, title, retryable = 502, "UPSTREAM_FAILURE", "All tiers failed", True


class UpstreamTimeout(FetcherError):
    status, code, title, retryable = 504, "UPSTREAM_TIMEOUT", "Per-request deadline exceeded", True


class RateLimited(FetcherError):
    status, code, title, retryable = 429, "RATE_LIMITED", "Per-key semaphore exhausted", True
