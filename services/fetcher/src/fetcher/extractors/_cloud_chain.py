"""Shared cloud-chain primitives + cache-key helpers for structurer endpoints.

Both `/v1/structure` (article cleanup) and the upcoming
`/v1/structure-transcript` (transcript normalization) share the same
OpenAI-compatible chain runner (Ollama Cloud -> OpenAI fallback), the same
hint-prepended user-message shape (title/author/date as context), and the same
content-keyed cache.

Lifted out of `extractors/structure.py` so the second endpoint can reuse the
runner, and so cache-key construction has one correct shape: the key covers
prompt content, chain config, and hint context, so editing the active
structurer prompt invalidates cache.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fetcher.fidelity import long_gaps_per_10k, trigram_recall


logger = logging.getLogger(__name__)


_KNOWN_PROVIDERS = {"openai", "ollama"}


# Floor on trigram recall, not on length: a model that paraphrases at constant
# volume passes any length check. The default leaves room for the article lane's
# boilerplate removal, which counts as loss against the whole-source denominator
# -- a chrome-heavy article legitimately scores near 57%. Transcript callers pass
# a tighter value; see `transcript_structurer`.
_MIN_RETENTION = 0.40


@dataclass(frozen=True)
class ChainEntry:
    model: str
    provider: str
    base_url: str | None = None
    attempt_timeout: float = 30.0


class StructurerChainFailed(Exception):
    """Raised when every entry in the cloud chain failed (or none were callable)."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class StructurerNotConfigured(StructurerChainFailed):
    """Permanent: structurer can't run because the chain config wasn't loaded
    or no provider in the chain has an API key. Distinct subclass so endpoint
    handlers can map it to 503 STRUCTURER_UNCONFIGURED via isinstance."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


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


def _model_params(model: str) -> dict[str, Any]:
    """Per-model completion parameters.

    gpt-5-family are reasoning models: they reject `temperature` with a 400 and
    take `reasoning_effort` instead. The two generations spell "lowest"
    differently — `gpt-5`/`gpt-5-mini` take `minimal`, the dotted `gpt-5.x`
    take `none`. Everything else takes the deterministic pair.
    """
    if model.startswith("gpt-5"):
        return {"reasoning_effort": "none" if model.startswith("gpt-5.") else "minimal"}
    return {"temperature": 0, "seed": 42}


def _key_for(provider: str, openai_key: str | None, ollama_key: str | None) -> str | None:
    if provider == "openai":
        return openai_key
    if provider == "ollama":
        return ollama_key
    return None


def _usage_payload(
    usage: Any, model: str, provider: str, duration_ms: float, finish_reason: str | None = None
) -> dict[str, Any]:
    """Capture per-call usage in the orchestrator's shape (tokens_in/out/cached).

    `finish_reason` separates a model that chose to stop ("stop") from one cut
    off at its output cap ("length") -- a truncated structuring silently loses
    the tail, and no prompt wording fixes that.

    `prompt_tokens_details.cached_tokens` may be absent on older response
    shapes or non-cached models; returns None rather than guessing.
    """
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
    cached_tokens: int | None = None
    if usage is not None:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached_tokens = getattr(details, "cached_tokens", None)
    return {
        "finish_reason": finish_reason,
        "provider": provider,
        "model": model,
        "tokens_in": prompt_tokens,
        "tokens_out": completion_tokens,
        "cached_tokens": cached_tokens,
        "duration_ms": round(duration_ms, 1),
    }


def _is_retryable(exc: BaseException) -> bool:
    """Classify whether a chain failure should retry. Default: transient."""
    from openai import APIStatusError

    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or exc.status_code in (408, 429)
    return True


async def call_cloud_chain(
    content: str,
    prompt: str,
    *,
    chain: list[ChainEntry],
    openai_key: str | None,
    ollama_key: str | None,
    title: str | None = None,
    content_date: str | None = None,
    author_name: str | None = None,
    min_retention: float = _MIN_RETENTION,
    max_gaps_per_10k: float | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Try each chain entry in order.

    Returns (markdown, "structurer:<model>", usage_payload). The usage payload
    carries per-call provenance (tokens_in/out/cached, duration_ms, provider,
    model) so the cache row records it for downstream observability, mirroring
    the orchestrator's `extraction_calls` shape.
    """
    from openai import AsyncOpenAI

    user_message = _build_user_message(
        content, title=title, content_date=content_date, author_name=author_name
    )

    callable_entries = [
        e for e in chain if _key_for(e.provider, openai_key, ollama_key) is not None
    ]
    match (chain, callable_entries):
        case ([], _):
            raise StructurerNotConfigured(
                "structurer chain config not loaded — no entries in YAML "
                "(check fetcher image has services/fetcher/config/ copied to /app/config/)"
            )
        case (_, []):
            raise StructurerNotConfigured(
                f"no API keys configured for any chain provider "
                f"(chain has {len(chain)} entries, none have a matching key)"
            )

    last_exc: BaseException | None = None
    last_retryable = True
    for entry in callable_entries:
        api_key = _key_for(entry.provider, openai_key, ollama_key)
        t0 = time.monotonic()
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=entry.base_url)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=entry.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_message},
                    ],
                    **_model_params(entry.model),
                ),
                timeout=entry.attempt_timeout,
            )
            duration_ms = (time.monotonic() - t0) * 1000
            markdown = (response.choices[0].message.content or "").strip()
            if not markdown:
                raise RuntimeError("empty completion")
            # A collapsed generation is fluent and passes every other check --
            # without this it would be cached and re-served for the whole TTL.
            # Raising falls through to the next chain entry for another draw.
            # Only the transcript lane sets `max_gaps_per_10k`; see
            # `fidelity.long_gaps_per_10k` for why it's not meaningful on the
            # article lane, where dropping a nav block or footer is correct.
            if max_gaps_per_10k is not None:
                gaps = long_gaps_per_10k(content, markdown)
                if gaps > max_gaps_per_10k:
                    raise RuntimeError(
                        f"rewritten completion: {gaps:.1f} contiguous gaps per 10k chars, "
                        f"ceiling {max_gaps_per_10k:.1f}"
                    )
            recall = trigram_recall(content, markdown)
            if recall < min_retention:
                raise RuntimeError(
                    f"collapsed completion: {100 * recall:.0f}% of the source's wording "
                    f"survived, floor {100 * min_retention:.0f}% "
                    f"({len(content)} chars in, {len(markdown)} out)"
                )
            usage = _usage_payload(
                getattr(response, "usage", None),
                entry.model,
                entry.provider,
                duration_ms,
                finish_reason=getattr(response.choices[0], "finish_reason", None),
            )
            logger.info(
                "structurer chain ok: provider=%s model=%s "
                "tokens_in=%s tokens_out=%s cached_tokens=%s duration_ms=%.0f chars=%d",
                entry.provider,
                entry.model,
                usage["tokens_in"],
                usage["tokens_out"],
                usage["cached_tokens"],
                duration_ms,
                len(markdown),
            )
            return markdown, f"structurer:{entry.model}", usage
        except Exception as exc:  # noqa: BLE001 — bounded fall-through across chain
            duration_ms = (time.monotonic() - t0) * 1000
            last_exc = exc
            last_retryable = _is_retryable(exc)
            logger.warning(
                "structurer chain failed: provider=%s model=%s duration_ms=%.0f %s: %.200s",
                entry.provider,
                entry.model,
                duration_ms,
                type(exc).__name__,
                exc,
            )
            continue

    detail = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "all entries failed"
    raise StructurerChainFailed(detail, retryable=last_retryable)


# ---------------------------------------------------------------------------
# Cache-key helpers
# ---------------------------------------------------------------------------


def content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_sha(text: str) -> str:
    """Hash of the prompt text. Use the in-memory prompt string the LLM sees,
    not the file on disk -- a hot-edited file with no restart still has the
    process running the old prompt, so cache hits stay semantically correct
    until restart reloads the file."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain_config_sha(chain: list[ChainEntry]) -> str:
    """Hash of the chain config in declaration order.

    Includes provider/model/base_url/attempt_timeout for every entry, not just
    the head -- reordering, swapping models, or changing per-entry knobs all
    invalidate cache.
    """
    serialised = [
        {
            "provider": e.provider,
            "model": e.model,
            "base_url": e.base_url,
            "attempt_timeout": e.attempt_timeout,
        }
        for e in chain
    ]
    blob = json.dumps(serialised, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_key_components(
    *,
    endpoint: str,
    content_sha_value: str,
    prompt_sha_value: str,
    chain_config_sha_value: str,
) -> str:
    """Compose the canonical cache key for structurer endpoints.

    Single source of truth so /v1/structure and /v1/structure-transcript stay
    consistent. The endpoint segment namespaces keys so identical content
    routed through different endpoints can't collide; hashing (prompt, chain,
    content) keeps the key compact while preserving the invalidation contract.
    """
    digest_input = f"{prompt_sha_value}|{chain_config_sha_value}|{content_sha_value}"
    combined = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    return f"{endpoint}:v2:{combined}"
