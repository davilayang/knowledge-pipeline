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
    extras: dict[str, Any] = field(default_factory=dict)
