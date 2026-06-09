"""RFC 7807 Problem Details body factory.

FastAPI-free: used by workers and cache for error serialization.
"""

from dataclasses import asdict
from typing import Any

from fetcher.types import Problem


_PROBLEM_TYPE_BASE = "https://fetcher/errors"


def problem_body(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    retryable: bool,
    retry_after_seconds: int | None = None,
    canonical_url: str | None = None,
    tier_log: list[Any] | None = None,
) -> dict[str, Any]:
    """Create a raw dict representing an RFC 7807 Problem Details object."""
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
    # Remove None values to keep the JSON clean
    return {k: v for k, v in body.items() if v is not None}
