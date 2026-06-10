"""Structurer cascade: trafilatura → markdown-passthrough heuristic → cloud LLM chain.

Content-keyed, not URL-keyed. Used by POST /v1/structure to clean noisy
user-pasted article bodies into structured markdown.
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fetcher.extractors import trafilatura as _trafilatura
from fetcher.types import FetchResult, TierLogEntry


if TYPE_CHECKING:
    from fetcher.types import FetchContext


_MIN_CHARS = 3000

_BOILERPLATE_PHRASES = (
    "sign in",
    "subscribe",
    "comments (0)",
    "share this",
    "related posts",
)

_HTML_TAG = re.compile(r"<[a-z][^>]{0,200}>", re.IGNORECASE)
_ALLOWED_VOID_TAGS = re.compile(r"<\s*(br|hr)\s*/?\s*>", re.IGNORECASE)
_HEADING_LINE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_BULLET = re.compile(r"^[-*]\s", re.MULTILINE)
_BOLD_RUN = re.compile(r"\*\*[^*]+\*\*")
_BLOCKQUOTE = re.compile(r"^>\s", re.MULTILINE)


class StructurerChainFailed(Exception):
    """Raised when every entry in the cloud chain failed (or none were callable)."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class StructurerCascadeFailed(Exception):
    """Raised when all three stages (trafilatura, passthrough, cloud) produced nothing."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        tier_log: list[TierLogEntry],
        last_error: str,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.tier_log = tier_log
        self.last_error = last_error


def _stage_trafilatura(raw_content: str) -> str | None:
    result = _trafilatura.extract(raw_content)
    if not result or len(result) < _MIN_CHARS:
        return None
    return result


def _stage_passthrough_heuristic(raw_content: str) -> str | None:
    if len(raw_content) < _MIN_CHARS:
        return None

    stripped_for_html = _ALLOWED_VOID_TAGS.sub("", raw_content)
    if len(_HTML_TAG.findall(stripped_for_html)) > 2:
        return None

    lowered = raw_content.lower()
    boilerplate_hits = sum(1 for phrase in _BOILERPLATE_PHRASES if phrase in lowered)
    if boilerplate_hits > 1:
        return None

    signals = 0
    if len(_HEADING_LINE.findall(raw_content)) >= 2:
        signals += 1
    if len(_LIST_BULLET.findall(raw_content)) >= 3:
        signals += 1
    if len(_BOLD_RUN.findall(raw_content)) >= 2:
        signals += 1
    if len(_BLOCKQUOTE.findall(raw_content)) >= 1:
        signals += 1

    if signals < 2:
        return None

    return raw_content


async def _call_cloud_chain(
    content: str,
    prompt: str,
    *,
    chain: list[Any],
    openai_key: str | None,
    ollama_key: str | None,
    title: str | None = None,
    content_date: str | None = None,
    author_name: str | None = None,
) -> tuple[str, str]:
    """Stub — real implementation lands in SF4."""
    raise StructurerChainFailed("cloud chain not yet implemented", retryable=False)


async def _stage_cloud_chain(
    ctx: "FetchContext",
    raw_content: str,
    *,
    prompt: str,
    title: str | None = None,
    content_date: str | None = None,
    author_name: str | None = None,
) -> tuple[str, str]:
    return await _call_cloud_chain(
        raw_content,
        prompt,
        chain=[],
        openai_key=getattr(ctx, "openai_api_key", None),
        ollama_key=getattr(ctx, "ollama_api_key", None),
        title=title,
        content_date=content_date,
        author_name=author_name,
    )


def _log_entry(tier: str, *, chars: int, error: str | None) -> TierLogEntry:
    return TierLogEntry(tier=tier, status=None, chars=chars, error=error, validated=chars > 0)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def run_cascade(
    ctx: "FetchContext",
    *,
    raw_content: str,
    source_url: str,
    title: str | None,
    content_date: str | None,
    author_name: str | None,
    prompt: str,
) -> FetchResult:
    """Run trafilatura → passthrough → cloud chain. Return FetchResult or raise."""
    tier_log: list[TierLogEntry] = []

    traf = _stage_trafilatura(raw_content)
    if traf is not None:
        tier_log.append(_log_entry("trafilatura", chars=len(traf), error=None))
        return FetchResult(
            markdown=traf,
            kind="structured",
            canonical_url=source_url,
            tier_used="trafilatura",
            fetched_at=_iso_now(),
            cache_hit=False,
            etag="",
            tier_log=tier_log,
            metadata={},
        )
    tier_log.append(_log_entry("trafilatura", chars=0, error="below floor or empty"))

    passthrough = _stage_passthrough_heuristic(raw_content)
    if passthrough is not None:
        tier_log.append(_log_entry("passthrough", chars=len(passthrough), error=None))
        return FetchResult(
            markdown=passthrough,
            kind="structured",
            canonical_url=source_url,
            tier_used="passthrough",
            fetched_at=_iso_now(),
            cache_hit=False,
            etag="",
            tier_log=tier_log,
            metadata={},
        )
    tier_log.append(_log_entry("passthrough", chars=0, error="heuristic rejected"))

    try:
        markdown, tier_name = await _stage_cloud_chain(
            ctx,
            raw_content,
            prompt=prompt,
            title=title,
            content_date=content_date,
            author_name=author_name,
        )
    except StructurerChainFailed as exc:
        tier_log.append(_log_entry("structurer", chars=0, error=str(exc)))
        raise StructurerCascadeFailed(
            "cascade exhausted",
            retryable=exc.retryable,
            tier_log=tier_log,
            last_error=str(exc),
        ) from exc

    tier_log.append(_log_entry(tier_name, chars=len(markdown), error=None))
    model_name = tier_name.removeprefix("structurer:") if tier_name.startswith("structurer:") else ""
    return FetchResult(
        markdown=markdown,
        kind="structured",
        canonical_url=source_url,
        tier_used=tier_name,
        fetched_at=_iso_now(),
        cache_hit=False,
        etag="",
        tier_log=tier_log,
        metadata={"model": model_name} if model_name else {},
    )
