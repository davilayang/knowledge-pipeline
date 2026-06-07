"""Article source: Jina Reader, then curl_cffi plus trafilatura."""

import logging
from urllib.parse import quote, urlparse

from fetcher.parsers import trafilatura as trafilatura_parser
from fetcher.types import FetchContext, RawTierResult, Tier


logger = logging.getLogger(__name__)

NAME = "article"
STRICT_PAID_TIER = False
_JINA_BASE = "https://r.jina.ai/"


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
    return True


def _validate_not_js_wall(content: str) -> bool:
    lowered = content.lower()
    return "please enable javascript" not in lowered and "you need to enable javascript" not in lowered


async def _jina_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    response = await ctx.jina_client.get(_JINA_BASE + quote(url, safe=""))
    if response.status_code >= 400:
        return RawTierResult(content="", status=response.status_code)
    return RawTierResult(content=response.text or "", status=response.status_code)


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

    return RawTierResult(content=trafilatura_parser.extract(html), status=status)


TIERS: list[Tier] = [
    Tier("jina", "free", 2000, 8000, _jina_fetch, validate=_validate_not_js_wall),
    Tier("curl_cffi", "free", 1500, 6000, _curl_cffi_trafilatura, validate=_validate_not_js_wall),
]
