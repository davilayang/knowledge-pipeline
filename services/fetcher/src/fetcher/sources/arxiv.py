"""arXiv source: pymupdf4llm free tier, then strict LlamaParse paid tier."""

import logging
import re
from urllib.parse import urlparse

import arxiv as arxiv_pypi

from fetcher.extractors import llamaparse as llamaparse_extractor
from fetcher.extractors import pymupdf as pymupdf_extractor
from fetcher.types import FetchContext, RawTierResult, Tier


logger = logging.getLogger(__name__)

NAME = "arxiv"
STRICT_PAID_TIER = True

_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
_NEW_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_OLD_ID_RE = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")


def _strip_pdf_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".pdf") else value


def _looks_like_id(candidate: str) -> bool:
    return bool(_NEW_ID_RE.match(candidate) or _OLD_ID_RE.match(candidate))


def matches(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _ARXIV_HOSTS:
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if path.startswith(("abs/", "pdf/")):
        path = path.split("/", 1)[1]
    return _looks_like_id(_strip_pdf_suffix(path))


def extract_arxiv_id(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path.startswith(("abs/", "pdf/")):
        path = path.split("/", 1)[1]
    path = _strip_pdf_suffix(path)
    match = _NEW_ID_RE.match(path) or _OLD_ID_RE.match(path)
    if not match:
        raise ValueError(f"not a recognisable arXiv ID in URL: {url!r}")
    return match.group(1)


def _format_header(meta: arxiv_pypi.Result, arxiv_id: str) -> str:
    authors = [author.name for author in meta.authors]
    primary = meta.primary_category or ""
    categories = list(meta.categories or [])
    other = [category for category in categories if category != primary]
    category_line = primary
    if other:
        category_line += f" ({', '.join(other)})"
    return (
        f"# {meta.title.strip()}\n\n"
        f"**Authors:** {', '.join(authors)}\n"
        f"**Published:** {meta.published.date().isoformat() if meta.published else ''}\n"
        f"**Categories:** {category_line}\n"
        f"**arXiv:** {arxiv_id}\n\n"
        f"## Abstract\n\n{meta.summary.strip()}\n\n---\n\n"
    )


async def _fetch_metadata(url: str) -> tuple[str, arxiv_pypi.Result] | None:
    try:
        arxiv_id = extract_arxiv_id(url)
    except ValueError as exc:
        logger.info("arxiv id extraction failed for %s: %s", url, exc)
        return None
    client = arxiv_pypi.Client(page_size=1, num_retries=1, delay_seconds=3)
    try:
        return arxiv_id, next(client.results(arxiv_pypi.Search(id_list=[arxiv_id])))
    except Exception as exc:
        logger.warning("arxiv metadata lookup failed for %s: %s", arxiv_id, exc)
        return None


async def _download_pdf(ctx: FetchContext, pdf_url: str) -> bytes:
    response = await ctx.http_client.get(pdf_url, timeout=ctx.default_timeout_s)
    response.raise_for_status()
    return response.content


async def _arxiv_pymupdf(ctx: FetchContext, url: str) -> RawTierResult:
    metadata = await _fetch_metadata(url)
    if metadata is None:
        return RawTierResult(content="", status=0)
    arxiv_id, paper = metadata
    if not paper.pdf_url:
        return RawTierResult(content="", status=0)
    try:
        pdf_bytes = await _download_pdf(ctx, paper.pdf_url)
    except Exception as exc:
        logger.warning("arxiv PDF download failed for %s: %s", paper.pdf_url, exc)
        return RawTierResult(content="", status=0)
    body = pymupdf_extractor.to_markdown(pdf_bytes)
    if not body:
        return RawTierResult(content="", status=0)
    return RawTierResult(content=_format_header(paper, arxiv_id) + body, status=200)


async def _arxiv_llamaparse(ctx: FetchContext, url: str) -> RawTierResult:
    metadata = await _fetch_metadata(url)
    if metadata is None:
        raise ValueError(f"arxiv metadata unresolvable for {url}")
    arxiv_id, paper = metadata
    if not paper.pdf_url:
        raise ValueError(f"arxiv paper has no PDF URL for {url}")
    body = await llamaparse_extractor.render_pdf(
        ctx.http_client,
        pdf_url=paper.pdf_url,
        api_key=ctx.llama_parse_api_key,
        tier=ctx.llama_parse_tier_arxiv,
    )
    return RawTierResult(content=_format_header(paper, arxiv_id) + body, status=200)


TIERS: list[Tier] = [
    Tier("pymupdf4llm", "free", 500, 10**9, _arxiv_pymupdf),
    Tier("llamaparse", "paid", 500, 500, _arxiv_llamaparse),
]
