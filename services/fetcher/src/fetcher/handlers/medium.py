"""Medium handler: Jina Reader free tier, then RapidAPI paywall bypass."""

import logging
import os
import re
from urllib.parse import urlparse

import yaml

from fetcher.extractors import jina as jina_extractor
from fetcher.extractors import rapidapi_medium as rapidapi_medium_extractor
from fetcher.types import FetchContext, RawTierResult, Tier
from fetcher.validator import is_acceptable


logger = logging.getLogger(__name__)

NAME = "medium"
STRICT_PAID_TIER = False

_ARTICLE_ID_RE = re.compile(r"-([0-9a-f]{8,12})$", re.IGNORECASE)


def _load_domains(path: str) -> set[str]:
    """Read the medium domains YAML and return a lowercased, www-stripped set.

    Fails fast on missing/empty file. A silent empty set (the previous
    behaviour) made the handler unreachable in prod whenever the YAML
    wasn't copied into the runtime image — Medium URLs then fell through
    to the article handler and never hit the paywall-bypass tier."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    domains = data.get("medium_domains") or []
    result: set[str] = set()
    for raw in domains:
        host = str(raw).strip().lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            result.add(host)
    if not result:
        raise RuntimeError(
            f"medium domains YAML at {path} produced an empty domain set; "
            f"medium handler would be unreachable"
        )
    return result


_MEDIUM_DOMAINS: set[str] = _load_domains(
    os.environ.get("FETCHER_MEDIUM_DOMAINS_PATH", "config/medium_domains.yaml")
)


def matches(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in _MEDIUM_DOMAINS


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
    return RawTierResult(content=body, status=status)


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
