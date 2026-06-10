"""Structurer cascade: trafilatura → markdown-passthrough heuristic → cloud LLM chain.

Content-keyed, not URL-keyed. Used by POST /v1/structure to clean noisy
user-pasted article bodies into structured markdown.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from fetcher.extractors import trafilatura as _trafilatura
from fetcher.types import FetchResult, TierLogEntry


if TYPE_CHECKING:
    from fetcher.types import FetchContext


logger = logging.getLogger(__name__)


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


_KNOWN_PROVIDERS = {"openai", "ollama"}


@dataclass(frozen=True)
class ChainEntry:
    model: str
    provider: str
    base_url: str | None = None
    attempt_timeout: float = 30.0


def _load_chain(path: Path) -> list[ChainEntry]:
    """Parse the structurer chain YAML. Returns [] if the file is missing."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("structurer chain YAML not found at %s; cloud stage unreachable", path)
        return []

    entries: list[ChainEntry] = []
    for raw in data.get("chain") or []:
        provider = str(raw["provider"])
        if provider not in _KNOWN_PROVIDERS:
            raise ValueError(f"unknown provider {provider!r} in {path}")
        entries.append(
            ChainEntry(
                model=str(raw["model"]),
                provider=provider,
                base_url=raw.get("base_url"),
                attempt_timeout=float(raw.get("attempt_timeout", 30.0)),
            )
        )
    return entries


_CHAIN: list[ChainEntry] = _load_chain(
    Path(os.environ.get("FETCHER_STRUCTURER_CONFIG_PATH", "config/structurer.yaml"))
)


def _load_prompt() -> str:
    path = Path(os.environ.get("FETCHER_STRUCTURER_PROMPT_PATH", "prompts/structure_v1.md"))
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.warning("structurer prompt not found at %s; cloud stage will use empty prompt", path)
        return ""


_PROMPT: str = _load_prompt()


def chain_head() -> tuple[str, str] | None:
    """Return (provider, model) of the primary chain entry, or None if chain empty."""
    if not _CHAIN:
        return None
    head = _CHAIN[0]
    return (head.provider, head.model)


def get_prompt() -> str:
    return _PROMPT


def get_chain() -> list[ChainEntry]:
    return list(_CHAIN)


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


def _build_user_message(
    raw_content: str,
    *,
    title: str | None,
    content_date: str | None,
    author_name: str | None,
) -> str:
    """Build the user-role message; hints are prepended as ground-truth context.

    Kept out of the system prompt so OpenAI prompt caching survives across calls.
    """
    hints: list[str] = []
    if title:
        hints.append(f"Title: {title}")
    if author_name:
        hints.append(f"Author: {author_name}")
    if content_date:
        hints.append(f"Date: {content_date}")
    if hints:
        return "\n".join(hints) + "\n\n---\n\n" + raw_content
    return raw_content


def _key_for(provider: str, openai_key: str | None, ollama_key: str | None) -> str | None:
    if provider == "openai":
        return openai_key
    if provider == "ollama":
        return ollama_key
    return None


async def _call_cloud_chain(
    content: str,
    prompt: str,
    *,
    chain: list[ChainEntry],
    openai_key: str | None,
    ollama_key: str | None,
    title: str | None = None,
    content_date: str | None = None,
    author_name: str | None = None,
) -> tuple[str, str]:
    """Try each chain entry in order. Returns (markdown, "structurer:<model>")."""
    from openai import AsyncOpenAI

    user_message = _build_user_message(
        content, title=title, content_date=content_date, author_name=author_name
    )

    callable_entries = [
        e for e in chain if _key_for(e.provider, openai_key, ollama_key) is not None
    ]
    if not callable_entries:
        raise StructurerChainFailed("no API keys configured", retryable=False)

    last_exc: BaseException | None = None
    last_retryable = True
    for entry in callable_entries:
        api_key = _key_for(entry.provider, openai_key, ollama_key)
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=entry.base_url)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=entry.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0,
                    seed=42,
                ),
                timeout=entry.attempt_timeout,
            )
            markdown = (response.choices[0].message.content or "").strip()
            if not markdown:
                raise RuntimeError("empty completion")
            return markdown, f"structurer:{entry.model}"
        except Exception as exc:  # noqa: BLE001 — bounded fall-through across chain
            last_exc = exc
            last_retryable = _is_retryable(exc)
            logger.warning(
                "structurer chain entry %s/%s failed: %s: %.200s",
                entry.provider,
                entry.model,
                type(exc).__name__,
                exc,
            )
            continue

    detail = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "all entries failed"
    raise StructurerChainFailed(detail, retryable=last_retryable)


def _is_retryable(exc: BaseException) -> bool:
    """Classify whether a chain failure should retry. Default: transient."""
    from openai import APIStatusError

    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or exc.status_code in (408, 429)
    return True


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
    model_name = (
        tier_name.removeprefix("structurer:") if tier_name.startswith("structurer:") else ""
    )
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
