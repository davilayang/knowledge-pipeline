"""Single application entry for fetch logic.

FastAPI-free: called by endpoints, workers, and future webhooks.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fetcher.cache import cache_key, compute_etag, lookup as cache_lookup, upsert as cache_upsert
from fetcher.canonicalize import canonicalize
from fetcher.cascade import run_cascade
from fetcher.errors import UnsupportedSource, UpstreamFailure
from fetcher.registry import find_source
from fetcher.single_flight import get_url_lock
from fetcher.types import FetchContext, FetchRequest, Source, TierLogEntry


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchOutcome:
    """Domain result of a fetch operation."""

    kind: str  # "success" | "pending" | "failure"
    markdown: str = ""
    source_type: str = ""
    canonical_url: str = ""
    tier_used: str = ""
    fetched_at: str = ""
    cache_hit: bool = False
    etag: str = ""
    tier_log: list[TierLogEntry] = None
    metadata: dict[str, Any] = None
    provider_job_id: str | None = None
    pending_tier: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cache_satisfies(source: Source, tier_used: str, markdown: str, quality: str) -> bool:
    tier = next((candidate for candidate in source.TIERS if candidate.name == tier_used), None)
    if tier is None:
        return False
    floor = tier.min_chars if quality == "fast" else tier.high_chars
    if len(markdown) < floor:
        return False
    return tier.validate is None or tier.validate(markdown)


async def run_fetch_request(
    req: FetchRequest,
    *,
    db_path: Path,
    ctx: FetchContext,
    ttl_days: int,
) -> FetchOutcome:
    """Run the fetch pipeline: canonicalize -> cache lookup -> cascade -> cache upsert."""
    source = find_source(req.url)
    if source is None:
        raise UnsupportedSource(f"no source matches URL: {req.url}")

    canonical = canonicalize(req.url).canonical_url
    lock = get_url_lock(cache_key(canonical))

    async with lock:
        if not req.force_refresh:
            cached = cache_lookup(db_path=db_path, canonical_url=canonical)
            if cached is not None and _cache_satisfies(
                source, cached.tier_used, cached.markdown, req.quality
            ):
                return FetchOutcome(
                    kind="success",
                    markdown=cached.markdown,
                    source_type=cached.source_type,
                    canonical_url=cached.canonical_url,
                    tier_used=cached.tier_used,
                    fetched_at=cached.fetched_at,
                    cache_hit=True,
                    etag=cached.etag,
                    tier_log=cached.tier_log,
                    metadata=cached.metadata,
                )

        cascade = await run_cascade(
            source,
            ctx,
            req.url,
            quality=req.quality,
            allow_paid=req.allow_paid,
        )

        if not cascade.content:
            raise UpstreamFailure(
                f"all tiers failed for {req.url}",
                canonical_url=canonical,
                tier_log=cascade.tier_log,
            )

        cache_upsert(
            db_path=db_path,
            canonical_url=canonical,
            source_type=source.NAME,
            markdown=cascade.content,
            tier_used=cascade.tier_used,
            metadata=cascade.metadata,
            tier_log=cascade.tier_log,
            ttl_days=ttl_days,
            url=req.url,
        )

        return FetchOutcome(
            kind="success",
            markdown=cascade.content,
            source_type=source.NAME,
            canonical_url=canonical,
            tier_used=cascade.tier_used,
            fetched_at=_now_iso(),
            cache_hit=False,
            etag=compute_etag(cascade.content),
            tier_log=cascade.tier_log,
            metadata=cascade.metadata,
        )
