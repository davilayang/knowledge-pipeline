"""PDF handler: pymupdf4llm free tier, then LlamaParse paid tier."""

import logging
from urllib.parse import urlparse

from fetcher.extractors import llamaparse as llamaparse_extractor
from fetcher.extractors import pymupdf as pymupdf_extractor
from fetcher.handlers._pdf_download import MAX_PDF_BYTES, PdfTooLarge, download_pdf_bytes
from fetcher.handlers.arxiv import _ARXIV_HOSTS
from fetcher.types import FetchContext, RawTierResult, Tier


logger = logging.getLogger(__name__)

NAME = "pdf"
STRICT_PAID_TIER = False

__all__ = ["MAX_PDF_BYTES", "TIERS", "matches"]


def matches(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.path.lower().endswith(".pdf"):
        return False
    host = (parsed.hostname or "").lower()
    if host in _ARXIV_HOSTS:
        return False
    return True


async def _pymupdf4llm_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    try:
        pdf_bytes, status = await download_pdf_bytes(
            ctx.http_client, url, timeout=ctx.upstream_timeout_s
        )
    except PdfTooLarge as exc:
        logger.warning("%s; aborting download", exc)
        return RawTierResult(content="", status=0)
    except Exception as exc:
        logger.warning("pdf download failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0)
    content = pymupdf_extractor.to_markdown(pdf_bytes)
    return RawTierResult(content=content, status=status)


async def _llamaparse_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    try:
        content = await llamaparse_extractor.render_pdf(
            ctx.http_client,
            pdf_url=url,
            api_key=ctx.llama_parse_api_key,
            tier=ctx.llama_parse_tier_pdf,
        )
    except ValueError as exc:
        logger.warning("llamaparse render failed for %s: %s", url, exc)
        return RawTierResult(content="", status=0)
    return RawTierResult(content=content, status=200)


TIERS: list[Tier] = [
    Tier("pymupdf4llm", "free", 1500, 8000, _pymupdf4llm_fetch),
    Tier("llamaparse", "paid", 2000, 10000, _llamaparse_fetch, rate_limit_key="llamaparse"),
]
