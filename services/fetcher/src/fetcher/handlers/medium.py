"""Medium handler: Jina Reader free tier, then RapidAPI paywall bypass."""

import logging
import re
from urllib.parse import urlparse

from domains.medium_urls import is_medium_url
from fetcher.extractors import jina as jina_extractor
from fetcher.extractors.rapidapi import medium as rapidapi_medium_extractor
from fetcher.types import FetchContext, RawTierResult, Tier
from fetcher.validator import is_acceptable

logger = logging.getLogger(__name__)

NAME = "medium"
STRICT_PAID_TIER = False

_ARTICLE_ID_RE = re.compile(r"-([0-9a-f]{8,12})$", re.IGNORECASE)


def matches(url: str) -> bool:
    # Medium host identity is the shared `domains.medium_urls` (known publication
    # hosts + `*.medium.com` author subdomains) — one source for the fetcher +
    # triage routing.
    return is_medium_url(url)


def extract_article_id(url: str) -> str:
    """Extract the trailing hex article ID from a Medium URL path."""
    parsed = urlparse(url)
    last_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    match = _ARTICLE_ID_RE.search(last_segment)
    if not match:
        raise ValueError(f"no medium article ID in URL: {url!r}")
    return match.group(1)


def _truncate(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


async def _jina_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    body, status = await jina_extractor.fetch(ctx.jina_client, url)
    if status >= 400:
        return RawTierResult(
            content="", status=status, detail=f"jina HTTP {status}: {_truncate(body)}"
        )
    if jina_extractor.wraps_upstream_error(body):
        return RawTierResult(
            content="",
            status=status,
            detail=f"jina wrapped upstream error: {_truncate(body)}",
        )
    # Capture the preamble's provenance (title + published date) BEFORE stripping
    # it — the error check above must run first (its marker lives in the preamble).
    return RawTierResult(
        content=jina_extractor.strip_preamble(body),
        status=status,
        metadata=jina_extractor.parse_preamble(body),
    )


async def _rapidapi_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    if not ctx.rapidapi_key:
        return RawTierResult(
            content="", status=0, detail="rapidapi skipped: RAPIDAPI_KEY not configured"
        )
    try:
        article_id = extract_article_id(url)
    except ValueError as exc:
        logger.warning("medium article-id extraction failed for %s: %s", url, exc)
        return RawTierResult(
            content="",
            status=0,
            detail=f"medium article-id extraction failed: {_truncate(str(exc))}",
        )
    try:
        content = await rapidapi_medium_extractor.fetch_markdown(
            ctx.http_client, article_id=article_id, api_key=ctx.rapidapi_key
        )
    except ValueError as exc:
        logger.warning("rapidapi medium fetch failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0, detail=f"rapidapi error: {_truncate(str(exc))}")
    return RawTierResult(content=content, status=200)


TIERS: list[Tier] = [
    Tier(
        "jina",
        "free",
        2000,
        8000,
        _jina_fetch,
        validate=is_acceptable,
        rate_limit_key="jina",
        # Jina's `Published Time:` preamble date is trustworthy even when the
        # paywall-stub body fails validation — carry it onto the rapidapi winner.
        carry_meta_on_reject=True,
    ),
    Tier(
        "rapidapi",
        "paid",
        2000,
        10000,
        _rapidapi_fetch,
        rate_limit_key="rapidapi",
    ),
]
