"""Facebook handler: RapidAPI facebook-scraper-api4, then facebook-scraper3.

Both tiers are RapidAPI-backed (no free fallback exists), so the handler
is STRICT_PAID_TIER — unauthorized fetches surface as a Problem instead
of silently dropping Facebook URLs to the article handler (which would
hit YouTube-style 'login required' content and produce truncated junk).
"""

import logging
from urllib.parse import urlparse

from fetcher.extractors.rapidapi import facebook_api4, facebook_scraper3
from fetcher.metadata import build_metadata
from fetcher.types import FetchContext, RawTierResult, Tier


logger = logging.getLogger(__name__)

NAME = "facebook"
STRICT_PAID_TIER = True

# Bare hosts allowed; any *.facebook.com subdomain also matches via suffix.
# Suffix match anchors on `.facebook.com` so spoofs like `facebook.example.com`
# stay rejected.
_FB_BARE_HOSTS = frozenset({"facebook.com", "fb.com", "fb.watch"})


def matches(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return False
    if host in _FB_BARE_HOSTS:
        return True
    return host.endswith(".facebook.com")


def _truncate(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


async def _api4_tier(ctx: FetchContext, url: str) -> RawTierResult:
    if not ctx.rapidapi_key:
        return RawTierResult(
            content="", status=0, detail="api4 skipped: RAPIDAPI_KEY not configured"
        )
    try:
        markdown, title, author = await facebook_api4.fetch_post(
            ctx.http_client, url=url, api_key=ctx.rapidapi_key
        )
    except ValueError as exc:
        logger.warning("facebook-scraper-api4 fetch failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0, detail=f"facebook_api4: {_truncate(str(exc))}")
    return RawTierResult(
        content=markdown,
        status=200,
        metadata=build_metadata(title=title, authors=author),
    )


async def _scraper3_tier(ctx: FetchContext, url: str) -> RawTierResult:
    if not ctx.rapidapi_key:
        return RawTierResult(
            content="", status=0, detail="scraper3 skipped: RAPIDAPI_KEY not configured"
        )
    pfbid = facebook_scraper3.extract_pfbid(url)
    if pfbid is None:
        # Older /posts/<numeric> + /share/p/<token> URLs don't carry pfbid;
        # this tier can't address them. Soft-skip — cascade records detail.
        return RawTierResult(content="", status=0, detail="scraper3: no pfbid in URL")
    try:
        markdown, title, author = await facebook_scraper3.fetch_post(
            ctx.http_client, pfbid=pfbid, api_key=ctx.rapidapi_key
        )
    except ValueError as exc:
        logger.warning("facebook-scraper3 fetch failed for %s: %s", url, exc)
        return RawTierResult(
            content="", status=0, detail=f"facebook_scraper3: {_truncate(str(exc))}"
        )
    return RawTierResult(
        content=markdown,
        status=200,
        metadata=build_metadata(title=title, authors=author),
    )


# Floors are deliberately low — FB posts are typically short (sometimes
# < 1 KB). The cascade's standard min_chars=2000/high_chars=8000 floors
# (article / medium) would reject every valid post as `below_floor`.
TIERS: list[Tier] = [
    Tier("facebook_api4", "paid", 200, 600, _api4_tier, rate_limit_key="rapidapi"),
    Tier("facebook_scraper3", "paid", 200, 600, _scraper3_tier, rate_limit_key="rapidapi"),
]
