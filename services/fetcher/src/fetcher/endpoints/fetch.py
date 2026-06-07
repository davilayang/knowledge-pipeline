"""POST /v1/fetch: synchronous single URL to markdown."""

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from fetcher.cache import cache_key, compute_etag, lookup, upsert
from fetcher.canonicalize import canonicalize
from fetcher.db import open_connection
from fetcher.errors import BadUrl, UnsupportedSource, UpstreamFailure
from fetcher.registry import find_source, run_cascade
from fetcher.single_flight import get_url_lock
from fetcher.types import Source, TierLogEntry


router = APIRouter()


class FetchRequest(BaseModel):
    url: str
    quality: str = "fast"
    allow_paid: bool = False
    force_refresh: bool = False


def _validate_url_shape(url: str) -> None:
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise BadUrl(f"malformed URL: {url}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadUrl(f"malformed URL: {url}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _tier_log_payload(tier_log: list[TierLogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "tier": entry.tier,
            "status": entry.status,
            "chars": entry.chars,
            "error": entry.error,
            "validated": entry.validated,
        }
        for entry in tier_log
    ]


def _build_response_body(
    *,
    markdown: str,
    source_type: str,
    canonical_url: str,
    tier_used: str,
    cache_hit: bool,
    etag: str,
    tier_log: list[TierLogEntry],
    metadata: dict[str, Any],
    fetched_at: str | None = None,
) -> dict[str, Any]:
    return {
        "markdown": markdown,
        "source_type": source_type,
        "canonical_url": canonical_url,
        "tier_used": tier_used,
        "fetched_at": fetched_at or _now_iso(),
        "cache_hit": cache_hit,
        "etag": etag,
        "tier_log": _tier_log_payload(tier_log),
        "metadata": metadata,
    }


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
    request: Request,
    if_none_match: str | None = None,
) -> JSONResponse | Response:
    """Shared implementation for HTTP and async worker call sites."""
    _validate_url_shape(req.url)
    source = find_source(req.url)
    if source is None:
        raise UnsupportedSource(f"no source matches URL: {req.url}")

    canonical = canonicalize(req.url).canonical_url
    settings = request.app.state.settings
    conn = open_connection(settings.db_path)
    try:
        lock = get_url_lock(cache_key(canonical))
        async with lock:
            if not req.force_refresh:
                cached = lookup(conn, canonical)
                if cached is not None and _cache_satisfies(
                    source, cached.tier_used, cached.markdown, req.quality
                ):
                    if if_none_match and if_none_match.strip('"') == cached.etag:
                        return Response(
                            status_code=304,
                            headers={"etag": f'"{cached.etag}"', "last-modified": cached.fetched_at},
                        )
                    body = _build_response_body(
                        markdown=cached.markdown,
                        source_type=cached.source_type,
                        canonical_url=cached.canonical_url,
                        tier_used=cached.tier_used,
                        cache_hit=True,
                        etag=cached.etag,
                        tier_log=cached.tier_log,
                        metadata=cached.metadata,
                        fetched_at=cached.fetched_at,
                    )
                    return JSONResponse(
                        content=body,
                        headers={
                            "etag": f'"{cached.etag}"',
                            "last-modified": cached.fetched_at,
                            "cache-control": f"private, max-age={settings.cache_ttl_days * 86400}",
                        },
                    )

            cascade = await run_cascade(
                source,
                request.app.state.fetch_context,
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

            upsert(
                conn,
                canonical_url=canonical,
                source_type=source.NAME,
                markdown=cascade.content,
                tier_used=cascade.tier_used,
                metadata={},
                tier_log=cascade.tier_log,
                ttl_days=settings.cache_ttl_days,
                url=req.url,
            )
            etag = compute_etag(cascade.content)
            body = _build_response_body(
                markdown=cascade.content,
                source_type=source.NAME,
                canonical_url=canonical,
                tier_used=cascade.tier_used,
                cache_hit=False,
                etag=etag,
                tier_log=cascade.tier_log,
                metadata={},
            )
            return JSONResponse(
                content=body,
                headers={
                    "etag": f'"{etag}"',
                    "last-modified": body["fetched_at"],
                    "cache-control": f"private, max-age={settings.cache_ttl_days * 86400}",
                },
            )
    finally:
        conn.close()


@router.post("/v1/fetch")
async def fetch(
    req: FetchRequest,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Any:
    return await run_fetch_request(req, request, if_none_match)
