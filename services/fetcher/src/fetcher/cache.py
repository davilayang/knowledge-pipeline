"""URL-hash-keyed cache with opportunistic eviction."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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


def cache_key(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def compute_etag(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_plus(days: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


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


def upsert(
    conn: sqlite3.Connection,
    *,
    canonical_url: str,
    source_type: str,
    markdown: str,
    tier_used: str,
    metadata: dict[str, Any],
    tier_log: list[TierLogEntry],
    ttl_days: int,
    url: str | None = None,
) -> None:
    """Upsert a cache row atomically. Last-writer-wins on conflict."""
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO cache (
            url_hash, url, canonical_url, source_type, markdown, etag,
            tier_used, content_chars, metadata_json, tier_log_json,
            fetched_at, expires_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url_hash) DO UPDATE SET
            url = excluded.url,
            canonical_url = excluded.canonical_url,
            source_type = excluded.source_type,
            markdown = excluded.markdown,
            etag = excluded.etag,
            tier_used = excluded.tier_used,
            content_chars = excluded.content_chars,
            metadata_json = excluded.metadata_json,
            tier_log_json = excluded.tier_log_json,
            fetched_at = excluded.fetched_at,
            expires_at = excluded.expires_at
        """,
        (
            cache_key(canonical_url),
            url or canonical_url,
            canonical_url,
            source_type,
            markdown,
            compute_etag(markdown),
            tier_used,
            len(markdown),
            json.dumps(metadata),
            _tier_log_to_json(tier_log),
            now,
            _iso_plus(ttl_days),
        ),
    )


def lookup(conn: sqlite3.Connection, canonical_url: str) -> CacheRow | None:
    """Return a row for the canonical URL, or None on miss/expired."""
    key = cache_key(canonical_url)
    row = conn.execute(
        """
        SELECT url_hash, url, canonical_url, source_type, markdown, etag,
               tier_used, content_chars, metadata_json, tier_log_json,
               fetched_at, expires_at
          FROM cache
         WHERE url_hash = ?
        """,
        (key,),
    ).fetchone()
    if row is None:
        return None

    if row[11] < _now_iso():
        conn.execute("DELETE FROM cache WHERE url_hash = ?", (key,))
        return None

    return CacheRow(
        url_hash=row[0],
        url=row[1],
        canonical_url=row[2],
        source_type=row[3],
        markdown=row[4],
        etag=row[5],
        tier_used=row[6],
        content_chars=row[7],
        metadata=json.loads(row[8]),
        tier_log=_tier_log_from_json(row[9]),
        fetched_at=row[10],
        expires_at=row[11],
    )
