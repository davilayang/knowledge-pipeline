# Thin wrapper around the OpenAI SDK with Langfuse drop-in tracing.

import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

_DEFAULT_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True, slots=True)
class LLMCall:
    """Single LLM invocation result with usage metadata.

    `cached_tokens` is the server-side prompt-cache hit (how many of the input
    tokens were served from OpenAI's prefix cache), so callers that share a
    cacheable prefix across calls can observe cache effectiveness. 0 when the
    provider reports no cache detail."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    finish_reason: str | None = None  # OpenAI stop reason; "length" == truncated


def _get_client() -> Any:
    """Return an OpenAI client, traced via Langfuse when configured.

    When LANGFUSE_PUBLIC_KEY is set, `langfuse.openai.OpenAI` is a transparent
    drop-in that auto-creates a generation observation for every call (nested
    under the active `@observe` span when there is one). When it is unset we
    return the plain `openai.OpenAI` so the module stays silent — no "client
    disabled" warning — exactly as before the Langfuse migration.

    Reads OPENAI_API_KEY from the environment automatically.
    """
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        from langfuse.openai import OpenAI
    else:
        from openai import OpenAI
    return OpenAI()


def _messages(prompt: str, system: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _usage(response: Any) -> tuple[int, int, int]:
    """(prompt_tokens, completion_tokens, cached_prompt_tokens).

    `cached_prompt_tokens` reads `usage.prompt_tokens_details.cached_tokens` — the
    portion of the prompt served from OpenAI's server-side prefix cache — and is 0
    when the provider omits that detail."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
    return usage.prompt_tokens or 0, usage.completion_tokens or 0, cached


def _create_kwargs(temperature: float | None) -> dict[str, Any]:
    """Optional create() kwargs. `temperature=None` omits it (provider default);
    a value pins it — pass 0 for a deterministic, faithful-extraction call."""
    return {} if temperature is None else {"temperature": temperature}


def generate(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
    temperature: float | None = None,
) -> str:
    """Generate a chat completion and return the assistant's text response."""
    response = _get_client().chat.completions.create(
        model=model, messages=_messages(prompt, system), **_create_kwargs(temperature)
    )
    return response.choices[0].message.content or ""


def generate_messages_with_usage(
    messages: list[dict[str, str]],
    *,
    model: str = _DEFAULT_MODEL,
    temperature: float | None = None,
) -> LLMCall:
    """Chat completion over a PRE-BUILT message list, returning content + usage.

    The entry point for prompt-cached calls: the caller controls the exact message
    order so the cacheable prefix (a shared system message + the source document)
    is byte-identical across sibling calls, with only the final task message
    differing. `cached_tokens` on the result reports the prefix-cache hit."""
    response = _get_client().chat.completions.create(
        model=model, messages=messages, **_create_kwargs(temperature)
    )
    in_tokens, out_tokens, cached = _usage(response)
    return LLMCall(
        content=response.choices[0].message.content or "",
        model=response.model or model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cached_tokens=cached,
        # OpenAI chat-completions always sets finish_reason on a choice ("length"
        # == hit max_tokens/truncated). This whole module is OpenAI-only (see
        # _get_client); getattr fails open (→ None → callers treat as not-truncated)
        # so a future non-OpenAI-compat client degrades safely rather than crashing.
        finish_reason=getattr(response.choices[0], "finish_reason", None),
    )


def generate_with_usage(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
    temperature: float | None = None,
) -> LLMCall:
    """Like generate(), but also returns token usage metadata."""
    return generate_messages_with_usage(
        _messages(prompt, system), model=model, temperature=temperature
    )


def generate_structured[
    T: BaseModel
](prompt: str, *, schema: type[T], system: str = "", model: str = _DEFAULT_MODEL,) -> T:
    """Generate a structured response validated against a Pydantic model.

    Uses OpenAI's native structured-output parse (`beta.chat.completions.parse`
    with `response_format=schema`); raises on a model refusal or empty parse so
    callers fail fast rather than receive a silent None.
    """
    parsed, _ = generate_structured_with_usage(prompt, schema=schema, system=system, model=model)
    return parsed


def generate_structured_with_usage[
    T: BaseModel
](
    prompt: str,
    *,
    schema: type[T],
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> tuple[
    T, LLMCall
]:
    """Like generate_structured(), but also returns token usage.

    One round-trip: `beta.chat.completions.parse` returns the validated model
    and usage together.
    """
    response = _get_client().beta.chat.completions.parse(
        model=model,
        messages=_messages(prompt, system),
        response_format=schema,
    )
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise ValueError(f"Model refused structured output: {message.refusal}")
    parsed = message.parsed
    if parsed is None:
        raise ValueError("Structured output parse returned no result")
    in_tokens, out_tokens, cached = _usage(response)
    return parsed, LLMCall(
        content=message.content or "",
        model=response.model or model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cached_tokens=cached,
    )
