"""Sync LLM cascade helper for orchestrator-side classifiers.

`run_cascade` tries a list of OpenAI-compatible chat-completion endpoints
in order, falling through on tier failure, never raising. Designed for
fast classifier-style calls (Groq → OpenAI today) where the caller wants
structured metadata about which tier answered rather than an exception
on full failure.

Separate from `services/fetcher/extractors/_cloud_chain.py` by design —
the fetcher copy is async, raises on full failure, and reads chain
config from YAML. Different abstraction; lives in a different repo
subtree (`services/`) that can't import from `packages/`.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class CascadeTier:
    """One step in the cascade: model name + OpenAI-compat endpoint + key."""

    model: str
    endpoint: str
    api_key: str


@dataclass(frozen=True, slots=True)
class CascadeResult[T]:
    """Outcome of a cascade run.

    `value` is None when every tier failed or no tiers were configured.
    `status` is one of: "ok", "skipped_no_tiers", "invalid_output", or
    any sentinel string the caller's `validate` returned.
    `model` names the tier that produced `value` (None when `value` is None).
    """

    value: T | None
    status: str
    model: str | None


def run_cascade[
    T
](
    *,
    tiers: list[CascadeTier],
    system_prompt: str,
    user_prompt: str,
    validate: Callable[[dict], tuple[T | None, str | None]],
    timeout_s: float = 30.0,
    max_completion_tokens: int = 200,
    temperature: float = 0.0,
) -> CascadeResult[T]:
    if not tiers:
        return CascadeResult(value=None, status="skipped_no_tiers", model=None)

    for tier in tiers:
        try:
            response = httpx.post(
                tier.endpoint,
                headers={
                    "Authorization": f"Bearer {tier.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": tier.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                    "max_completion_tokens": max_completion_tokens,
                },
                timeout=timeout_s,
            )
            payload_str = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(payload_str)
            value, sentinel = validate(payload)
        except Exception:
            # Network blip, malformed JSON, missing key in payload — all
            # treated as tier-skip. Caller wants resilience, not a stack
            # trace, when one provider misbehaves.
            continue
        if value is None and sentinel is None:
            continue
        status = sentinel if sentinel is not None else "ok"
        return CascadeResult(value=value, status=status, model=tier.model)

    return CascadeResult(value=None, status="invalid_output", model=None)
