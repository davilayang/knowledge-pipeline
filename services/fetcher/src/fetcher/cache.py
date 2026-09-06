"""Service-side cache adapter — typed wrapper over fetch_store.cache_* ops.

The SQL + connection management lives in domains.fetch_store.sources.
This module exists to adapt between the typed service-layer view
(CacheRow + TierLogEntry) and the JSON-shaped persistence view.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domains.fetch_store.sources import (
    _cache_key as cache_key,
    _etag as compute_etag,
    cache_lookup as _store_lookup,
    cache_upsert as _store_upsert,
    canonicalize_lookup as _alias_lookup,
    canonicalize_upsert as _alias_upsert,
)

from fetcher.canonicalize import CanonicalResult, canonicalize
from fetcher.types import TierLogEntry


__all__ = [
    "CacheRow",
    "cache_key",
    "canonicalize_cached",
    "compute_etag",
    "lookup",
    "upsert",
]


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
                "duration_ms": entry.duration_ms,
                "floor": entry.floor,
                "error_kind": entry.error_kind,
                "detail": entry.detail,
            }
            for entry in tier_log
        ]
    )


def _tier_log_from_json(raw: str) -> list[TierLogEntry]:
    # `.get(...)` on the enriched fields so rows written by older builds
    # still deserialize. Defaults match the dataclass defaults.
    return [
        TierLogEntry(
            tier=entry["tier"],
            status=entry["status"],
            chars=entry["chars"],
            error=entry["error"],
            validated=entry["validated"],
            duration_ms=entry.get("duration_ms", 0),
            floor=entry.get("floor"),
            error_kind=entry.get("error_kind"),
            detail=entry.get("detail"),
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


def canonicalize_cached(
    url: str,
    *,
    db_path: Path,
    ttl_days: int,
    force_refresh: bool = False,
) -> tuple[CanonicalResult, bool]:
    """Canonicalize `url`, reading and writing the url_aliases cache.

    Returns the result and whether it came from cache. `canonicalize()` makes a
    blocking HTTP round trip to the origin to follow redirects; a live alias row
    answers without one.

    A result whose redirect-follow failed is returned but never persisted: its
    canonical_url is the input URL echoed back, and storing that would pin a
    transient network error into the alias table — and, because the canonical
    URL is also the content cache key, file the fetched markdown under the
    wrong key until the row expired.
    """
    # Same sha256-hex helper the content cache keys on, applied to the *input*
    # URL: the alias table is what maps that to a canonical one.
    key = cache_key(url)

    if not force_refresh:
        row = _alias_lookup(db_path=db_path, input_url_hash=key)
        if row is not None:
            return (
                CanonicalResult(
                    input_url=row["input_url"],
                    canonical_url=row["canonical_url"],
                    redirects_followed=json.loads(row["redirects_json"]),
                    params_stripped=json.loads(row["params_stripped"]),
                ),
                True,
            )

    result = canonicalize(url)
    if result.resolved:
        _alias_upsert(
            db_path=db_path,
            input_url_hash=key,
            input_url=result.input_url,
            canonical_url=result.canonical_url,
            redirects_json=json.dumps(result.redirects_followed),
            params_stripped=json.dumps(result.params_stripped),
            ttl_days=ttl_days,
        )
    return result, False
