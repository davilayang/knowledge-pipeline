"""Shared FetchResult type for per-type fetcher modules + the resource layer."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FetchResult:
    content: str = ""
    tier: str = ""
    tier_log: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    author: str = ""
    error: str = ""
    # True only when the fetcher is confident the error is transient (upstream
    # 5xx, blocked IP, connection drop). Default False = treat as permanent
    # and fail fast — Dagster RetryPolicy is gated on this at the asset.
    transient: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
