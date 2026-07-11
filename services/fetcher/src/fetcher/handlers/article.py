"""Article handler: Jina Reader, then curl_cffi plus trafilatura, then Tavily Extract."""

import logging
from urllib.parse import urlparse

from fetcher.extractors import jina as jina_extractor
from fetcher.extractors import tavily as tavily_extractor
from fetcher.extractors import trafilatura as trafilatura_extractor
from fetcher.types import FetchContext, RawTierResult, Tier
from fetcher.validator import is_acceptable

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
    bare_host = host.removeprefix("www.")
    if bare_host in {"facebook.com", "fb.com", "fb.watch"} or bare_host.endswith(".facebook.com"):
        return False
    from fetcher.handlers import medium as medium_handler

    if medium_handler.matches(url):
        return False
    return True


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


async def _curl_cffi_trafilatura(ctx: FetchContext, url: str) -> RawTierResult:
    from curl_cffi.requests import AsyncSession

    proxies = {"https": ctx.socks5_url, "http": ctx.socks5_url} if ctx.socks5_url else None
    last_exc: Exception | None = None
    try:
        async with AsyncSession(impersonate="safari17_0") as session:
            response = await session.get(url, proxies=proxies, timeout=ctx.upstream_timeout_s)
            html = response.text or ""
            status = response.status_code
    except Exception as exc:
        last_exc = exc
        if not proxies:
            logger.warning("curl_cffi fetch failed for %s: %s", url, exc)
            return RawTierResult(
                content="",
                status=0,
                detail=f"curl_cffi: {type(exc).__name__}: {_truncate(str(exc))}",
            )
        logger.warning("curl_cffi proxied fetch failed for %s, retrying direct: %s", url, exc)
        try:
            async with AsyncSession(impersonate="safari17_0") as session:
                response = await session.get(url, timeout=ctx.upstream_timeout_s)
                html = response.text or ""
                status = response.status_code
        except Exception as direct_exc:
            logger.warning("curl_cffi direct fetch failed for %s: %s", url, direct_exc)
            return RawTierResult(
                content="",
                status=0,
                detail=(
                    f"curl_cffi proxied+direct failed; proxied={type(last_exc).__name__}: "
                    f"{_truncate(str(last_exc))}; direct={type(direct_exc).__name__}: "
                    f"{_truncate(str(direct_exc))}"
                ),
            )

    # Trafilatura happily extracts boilerplate from 4xx error pages — short-circuit.
    if status >= 400:
        return RawTierResult(
            content="",
            status=status,
            detail=f"curl_cffi HTTP {status}: {_truncate(html)}",
        )
    extracted = trafilatura_extractor.extract(html)
    return RawTierResult(
        content=extracted,
        status=status,
        detail=None if extracted else "trafilatura produced empty markdown from 2xx html",
    )


async def _tavily_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    if not ctx.tavily_api_key:
        return RawTierResult(
            content="", status=0, detail="tavily skipped: TAVILY_API_KEY not configured"
        )
    try:
        content = await tavily_extractor.extract(
            ctx.http_client, url=url, api_key=ctx.tavily_api_key
        )
    except ValueError as exc:
        logger.warning("tavily extract failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0, detail=f"tavily error: {_truncate(str(exc))}")
    return RawTierResult(
        content=content,
        status=200 if content else 0,
        detail=None if content else "tavily returned empty content",
    )


TIERS: list[Tier] = [
    Tier(
        "jina",
        "free",
        2000,
        8000,
        _jina_fetch,
        validate=is_acceptable,
        rate_limit_key="jina",
    ),
    Tier("curl_cffi", "free", 1500, 6000, _curl_cffi_trafilatura, validate=is_acceptable),
    Tier(
        "tavily",
        "paid",
        1500,
        6000,
        _tavily_fetch,
        validate=is_acceptable,
        rate_limit_key="tavily",
    ),
]
