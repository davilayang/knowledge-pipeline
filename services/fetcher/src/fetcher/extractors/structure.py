"""Structurer cascade: trafilatura → cloud LLM chain.

Content-keyed, not URL-keyed. Used by POST /v1/structure to clean noisy
user-pasted article bodies into structured markdown.

Shared chain runner + cache-key helpers live in `_cloud_chain.py` so the
upcoming `/v1/structure-transcript` endpoint can reuse them. This module
re-exports `ChainEntry`, `StructurerChainFailed`, and `_load_chain` for
existing callers and tests.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fetcher.extractors import trafilatura as _trafilatura
from fetcher.extractors._cloud_chain import (
    ChainEntry,
    StructurerChainFailed,
    StructurerNotConfigured,
    _load_chain,
    call_cloud_chain,
)
from fetcher.types import FetchResult, TierLogEntry


if TYPE_CHECKING:
    from fetcher.types import FetchContext


__all__ = [
    "ChainEntry",
    "StructurerCascadeFailed",
    "StructurerChainFailed",
    "_load_chain",
    "chain_head",
    "get_chain",
    "get_prompt",
    "run_cascade",
]


logger = logging.getLogger(__name__)


_MIN_CHARS = 3000


_CHAIN: list[ChainEntry] = _load_chain(
    Path(os.environ.get("FETCHER_STRUCTURER_CONFIG_PATH", "config/structurer.yaml"))
)


_PROMPT_PATH: Path = Path(
    os.environ.get("FETCHER_STRUCTURER_PROMPT_PATH", "prompts/structure_v1.md")
)


def _load_prompt(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.warning("structurer prompt not found at %s; cloud stage will use empty prompt", path)
        return ""


_PROMPT: str = _load_prompt(_PROMPT_PATH)


def chain_head() -> tuple[str, str] | None:
    """Return (provider, model) of the primary chain entry, or None if chain empty."""
    if not _CHAIN:
        return None
    head = _CHAIN[0]
    return (head.provider, head.model)


def get_prompt() -> str:
    return _PROMPT


def get_prompt_path() -> Path:
    return _PROMPT_PATH


def get_chain() -> list[ChainEntry]:
    return list(_CHAIN)


class StructurerCascadeFailed(Exception):
    """Raised when both stages (trafilatura, cloud) produced nothing."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        tier_log: list[TierLogEntry],
        last_error: str,
        not_configured: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.tier_log = tier_log
        self.last_error = last_error
        self.not_configured = not_configured


def _stage_trafilatura(raw_content: str) -> str | None:
    result = _trafilatura.extract(raw_content)
    if not result or len(result) < _MIN_CHARS:
        return None
    return result



async def _stage_cloud_chain(
    ctx: "FetchContext",
    raw_content: str,
    *,
    prompt: str,
    title: str | None = None,
    content_date: str | None = None,
    author_name: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    return await call_cloud_chain(
        raw_content,
        prompt,
        chain=_CHAIN,
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
    """Run trafilatura → cloud chain. Return FetchResult or raise."""
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

    try:
        markdown, tier_name, usage = await _stage_cloud_chain(
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
            not_configured=isinstance(exc, StructurerNotConfigured),
        ) from exc

    tier_log.append(_log_entry(tier_name, chars=len(markdown), error=None))
    model_name = (
        tier_name.removeprefix("structurer:") if tier_name.startswith("structurer:") else ""
    )
    metadata: dict[str, Any] = {"usage": usage}
    if model_name:
        metadata["model"] = model_name
    return FetchResult(
        markdown=markdown,
        kind="structured",
        canonical_url=source_url,
        tier_used=tier_name,
        fetched_at=_iso_now(),
        cache_hit=False,
        etag="",
        tier_log=tier_log,
        metadata=metadata,
    )
