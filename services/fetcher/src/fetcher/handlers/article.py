"""Article handler: Jina Reader, then curl_cffi plus trafilatura, then Tavily Extract."""

import logging
from urllib.parse import urlparse

from fetcher.extractors import jina as jina_extractor
from fetcher.extractors import tavily as tavily_extractor
from fetcher.extractors import trafilatura as trafilatura_extractor
from fetcher.types import FetchContext, RawTierResult, Tier


logger = logging.getLogger(__name__)

NAME = "article"
STRICT_PAID_TIER = False


def matches(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.path.lower().endswith(".pdf"):
        return False
    host = (parsed.hostname or "").lower()
    if "arxiv.org" in host:
        return False
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
        return False
    from fetcher.handlers import medium as medium_handler

    if medium_handler.matches(url):
        return False
    return True


def _validate_not_js_wall(content: str) -> bool:
    lowered = content.lower()
    return (
        "please enable javascript" not in lowered and "you need to enable javascript" not in lowered
    )


async def _jina_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    body, status = await jina_extractor.fetch(ctx.jina_client, url)
    if status >= 400 or jina_extractor.wraps_upstream_error(body):
        return RawTierResult(content="", status=status)
    return RawTierResult(content=body, status=status)


async def _curl_cffi_trafilatura(ctx: FetchContext, url: str) -> RawTierResult:
    from curl_cffi.requests import AsyncSession

    proxies = {"https": ctx.socks5_url, "http": ctx.socks5_url} if ctx.socks5_url else None
    try:
        async with AsyncSession(impersonate="safari17_0") as session:
            response = await session.get(url, proxies=proxies, timeout=ctx.default_timeout_s)
            html = response.text or ""
            status = response.status_code
    except Exception as exc:
        if not proxies:
            logger.warning("curl_cffi fetch failed for %s: %s", url, exc)
            return RawTierResult(content="", status=0)
        logger.warning("curl_cffi proxied fetch failed for %s, retrying direct: %s", url, exc)
        try:
            async with AsyncSession(impersonate="safari17_0") as session:
                response = await session.get(url, timeout=ctx.default_timeout_s)
                html = response.text or ""
                status = response.status_code
        except Exception as direct_exc:
            logger.warning("curl_cffi direct fetch failed for %s: %s", url, direct_exc)
            return RawTierResult(content="", status=0)

    # Trafilatura happily extracts boilerplate from 4xx error pages — short-circuit.
    if status >= 400:
        return RawTierResult(content="", status=status)
    return RawTierResult(content=trafilatura_extractor.extract(html), status=status)


async def _tavily_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    if not ctx.tavily_api_key:
        return RawTierResult(content="", status=0)
    try:
        content = await tavily_extractor.extract(
            ctx.http_client, url=url, api_key=ctx.tavily_api_key
        )
    except ValueError as exc:
        logger.warning("tavily extract failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0)
    return RawTierResult(content=content, status=200)


TIERS: list[Tier] = [
    Tier(
        "jina",
        "free",
        2000,
        8000,
        _jina_fetch,
        validate=_validate_not_js_wall,
        rate_limit_key="jina",
    ),
    Tier("curl_cffi", "free", 1500, 6000, _curl_cffi_trafilatura, validate=_validate_not_js_wall),
    Tier(
        "tavily",
        "paid",
        1500,
        6000,
        _tavily_fetch,
        validate=_validate_not_js_wall,
        rate_limit_key="tavily",
    ),
]
