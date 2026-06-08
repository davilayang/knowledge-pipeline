"""Service-side cache adapter — typed wrapper over fetches_store.cache_* ops.

The SQL + connection management lives in domains.fetches_store.sources.
This module exists to adapt between the typed service-layer view
(CacheRow + TierLogEntry) and the JSON-shaped persistence view.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domains.fetches_store.sources import (
    _cache_key as cache_key,
)
from domains.fetches_store.sources import (
    _etag as compute_etag,
)
from domains.fetches_store.sources import (
    cache_lookup as _store_lookup,
)
from domains.fetches_store.sources import (
    cache_upsert as _store_upsert,
)

from fetcher.types import TierLogEntry


@dataclass(frozen=True)
class CacheRow:
    url_hash: str
    url: str
    canonical_url: str
    source_type: str
    markdown: str
    etag: str
    tier_used: str
    content_chars: int
    metadata: dict[str, Any]
    tier_log: list[TierLogEntry]
    fetched_at: str
    expires_at: str


def _tier_log_to_json(tier_log: list[TierLogEntry]) -> str:
    return json.dumps(
        [
            {
                "tier": entry.tier,
                "status": entry.status,
                "chars": entry.chars,
                "error": entry.error,
                "validated": entry.validated,
            }
            for entry in tier_log
        ]
    )


def _tier_log_from_json(raw: str) -> list[TierLogEntry]:
    return [
        TierLogEntry(
            tier=entry["tier"],
            status=entry["status"],
            chars=entry["chars"],
            error=entry["error"],
            validated=entry["validated"],
        )
        for entry in json.loads(raw)
    ]


def lookup(*, db_path: Path, canonical_url: str) -> CacheRow | None:
    """Read the cache row for a canonical URL, or None on miss/expired."""
    row = _store_lookup(db_path=db_path, canonical_url=canonical_url)
    if row is None:
        return None
    return CacheRow(
        url_hash=row["url_hash"],
        url=row["url"],
        canonical_url=row["canonical_url"],
        source_type=row["source_type"],
        markdown=row["markdown"],
        etag=row["etag"],
        tier_used=row["tier_used"],
        content_chars=row["content_chars"],
        metadata=json.loads(row["metadata_json"]),
        tier_log=_tier_log_from_json(row["tier_log_json"]),
        fetched_at=row["fetched_at"],
        expires_at=row["expires_at"],
    )


def upsert(
    *,
    db_path: Path,
    canonical_url: str,
    source_type: str,
    markdown: str,
    tier_used: str,
    metadata: dict[str, Any],
    tier_log: list[TierLogEntry],
    ttl_days: int,
    url: str | None = None,
) -> None:
    """Insert or replace a cache row. Last-writer-wins on conflict."""
    _store_upsert(
        db_path=db_path,
        canonical_url=canonical_url,
        source_type=source_type,
        markdown=markdown,
        tier_used=tier_used,
        metadata_json=json.dumps(metadata),
        tier_log_json=_tier_log_to_json(tier_log),
        ttl_days=ttl_days,
        url=url,
    )
