"""FetchContext factory for lifespan-managed HTTP clients."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from fetcher.config import Settings
from fetcher.types import FetchContext


@asynccontextmanager
async def make_fetch_context(settings: Settings) -> AsyncIterator[FetchContext]:
    """Yield a FetchContext and close its clients on exit."""
    timeout = httpx.Timeout(float(settings.default_timeout_s))
    default = httpx.AsyncClient(timeout=timeout)
    jina = httpx.AsyncClient(
        timeout=timeout,
        headers={"Authorization": f"Bearer {settings.jina_api_key}"},
    )
    try:
        yield FetchContext(
            http_client=default,
            jina_client=jina,
            socks5_url=settings.socks5_url,
            llama_parse_api_key=settings.llama_parse_api_key,
            llama_parse_tier_arxiv=settings.llama_parse_tier_arxiv,
            default_timeout_s=settings.default_timeout_s,
        )
    finally:
        await default.aclose()
        await jina.aclose()
