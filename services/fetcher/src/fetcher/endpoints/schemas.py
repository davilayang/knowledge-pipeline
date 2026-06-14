"""Pydantic models for FastAPI-visible response shapes.

These exist so OpenAPI (`/openapi.json`, `/docs`) shows typed schemas
instead of `application/json`-flavoured opaque blobs. The actual runtime
bodies are built by `fetcher.problems.problem_body` and the per-endpoint
success serializers — these models mirror that shape for documentation
purposes, not as the source of truth.
"""

from typing import Any

from pydantic import BaseModel, Field


class ProblemResponse(BaseModel):
    """RFC 7807 Problem+JSON error envelope returned by all error paths.

    Built by `fetcher.problems.problem_body`. Wired to per-endpoint
    `responses={4xx: {"model": ProblemResponse}, ...}` so /docs shows the
    real shape callers should code against.
    """

    type: str = Field(
        description="Stable URI identifying the error class (e.g. .../errors/structurer-upstream-failure).",
        examples=["https://fetcher/errors/structurer-upstream-failure"],
    )
    title: str = Field(
        description="Short, human-readable summary of the problem.",
        examples=["Structurer cascade exhausted"],
    )
    status: int = Field(description="HTTP status code echoing the response line.", examples=[502])
    code: str = Field(
        description="Machine-readable error code; primary discriminator for clients.",
        examples=["STRUCTURER_UPSTREAM_FAILURE"],
    )
    detail: str = Field(
        description="Specific reason this occurrence happened (free text, may include upstream message).",
        examples=["TimeoutError: chain entry ollama:gemma4:31b exceeded 600s"],
    )
    instance: str = Field(
        description="Path of the request that produced this problem.",
        examples=["/v1/structure-transcript"],
    )
    retryable: bool = Field(
        description="Whether the same request can plausibly succeed if retried.",
        examples=[True],
    )
    retry_after_seconds: int | None = Field(
        default=None,
        description="Hint for how long the caller should wait before retrying, if known.",
    )
    canonical_url: str | None = Field(
        default=None,
        description="Canonicalized URL associated with the request, when applicable.",
    )
    tier_log: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-tier provenance for fetch/structurer failures; empty for non-cascade errors.",
    )
