"""Shared dataclasses and protocols used across sources, tiers, cache, and endpoints."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx


Quality = Literal["fast", "high"]
Cost = Literal["free", "paid"]


@dataclass
class FetchContext:
    """Lifetime-shared clients and config passed into tier functions."""

    http_client: httpx.AsyncClient
    jina_client: httpx.AsyncClient
    socks5_url: str
    llama_parse_api_key: str
    llama_parse_tier_arxiv: str
    default_timeout_s: int


@dataclass(frozen=True)
class TierLogEntry:
    tier: str
    status: int | None
    chars: int
    error: str | None
    validated: bool


@dataclass
class RawTierResult:
    """Raw markdown and provenance returned by one tier."""

    content: str
    status: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    """Response shape returned on a successful fetch."""

    markdown: str
    source_type: str
    canonical_url: str
    tier_used: str
    fetched_at: str
    cache_hit: bool
    etag: str
    tier_log: list[TierLogEntry]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Tier:
    """One step in a source cascade."""

    name: str
    cost: Cost
    min_chars: int
    high_chars: int
    run: Callable[[FetchContext, str], Awaitable[RawTierResult]]
    validate: Callable[[str], bool] | None = None
    applies: Callable[[str], bool] | None = None


class Source(Protocol):
    """Protocol every source module conforms to."""

    NAME: str
    TIERS: list[Tier]
    STRICT_PAID_TIER: bool

    @staticmethod
    def matches(url: str) -> bool: ...


@dataclass
class Problem:
    """RFC 7807 Problem Details."""

    type: str
    title: str
    status: int
    code: str
    detail: str
    instance: str
    retryable: bool
    retry_after_seconds: int | None = None
    canonical_url: str | None = None
    tier_log: list[TierLogEntry] = field(default_factory=list)
