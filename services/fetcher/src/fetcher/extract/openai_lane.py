"""Running the extraction tasks against OpenAI.

Everything OpenAI-shaped is here on purpose — message layout, token kwargs, the
JSON-mode contract, the cache-key hint, and the decision to run tasks one after
another. All four are consequences of how one vendor caches, not of what
extraction is, and a second provider would answer each of them differently:
Anthropic marks its cached prefix explicitly and can fan out after priming,
where OpenAI matches byte-identical prefixes and so must serialise.

Keeping them in one module is what makes adding that second lane a new file plus
a dispatch, rather than an edit spread through the request path.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from fetcher.extract.shared import (
    ARTICLE_ENVELOPE,
    SHARED_SYSTEM,
    render_envelope,
    validate_strict,
)
from fetcher.extract.tasks import TaskSpec


logger = logging.getLogger(__name__)


# Routing hint on every call: OpenAI steers requests sharing a key to the same
# cache, so one item's tasks land on one machine and share the article prefix.
EXTRACTION_CACHE_KEY = "kp-extraction"

# JSON mode guarantees syntactically valid json, not a reply that satisfies the
# pydantic model, so each call validates its own output and re-asks on failure.
# Three attempts: a model that has missed the schema twice is unlikely to find
# it on a fourth try.
_MAX_ATTEMPTS = 3

# The widest narrative measured spends 2,131 of this against the current prompt,
# on the worst of three real long sources (70k-233k characters). Not a request
# field: a caller cannot know the ceiling a prompt needs, and the eval harness
# measuring a different one from production would make its numbers unusable.
MAX_TOKENS = 4096

# Appended to the followups task tail only when the caller supplies user_notes.
READER_THREADS_FOLD = (
    "\n\n---\n"
    "The user message may include a `[reader's notes — NOT part of the source "
    "article]` block: the reader's own annotations, NOT source content. Populate "
    "`reader_threads` with each note restated as the reader's own thread (a focus "
    "they asked for, an open-loop/action, or context they gave). Never answer reader "
    "notes from the source, never invent threads, and never treat a note as a fact "
    "stated by the source. Leave `reader_threads` empty if the block is absent."
)


@dataclass
class TaskOutcome:
    """What one task produced — a payload or a reason it has none, never both."""

    task: str
    prompt_label: str
    prompt_sha256: str
    payload: Any = None
    error: str | None = None
    retryable: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int | None = None
    duration_ms: float = 0.0


def token_kwargs(model: str, max_tokens: int) -> dict[str, Any]:
    """Token-budget kwargs for an extraction call, per model family.

    gpt-5-family are reasoning models: they reject `max_tokens` and need
    `max_completion_tokens`, plus the lowest reasoning effort — extraction wants
    coverage of the source, not deliberation. gpt-4.1/4o keep the classic
    `max_tokens` and reject `reasoning_effort` entirely.

    The two gpt-5 generations spell "lowest" differently and reject each other's
    value, so one spelling would 400 a whole generation on every call. Verified
    live: `gpt-5`/`gpt-5-mini` take `minimal`; `gpt-5.4-*` and `gpt-5.6-*` take
    `none`. Splitting on the dot means an unknown dotted release gets the newer
    spelling and fails safe. `gpt-5-chat-*` would be mis-routed; none is in use.
    """
    if model.startswith("gpt-5"):
        effort = "none" if model.startswith("gpt-5.") else "minimal"
        return {"max_completion_tokens": max_tokens, "reasoning_effort": effort}
    return {"max_tokens": max_tokens}


def schema_block(schema: type) -> str:
    """The output contract appended to a call's task tail, generated from the
    pydantic model.

    JSON mode never shows the model a schema, so the prompt must carry one;
    generating it keeps `domains.extraction.schemas` the single source of truth.
    It also supplies the literal word "json" the API requires in the messages.

    Deliberately in the tail rather than promoted to a strict `response_format`
    json_schema: the tail is what keeps the article prefix byte-identical across
    tasks, and a per-task schema in the prefix would cost the cache on every one.
    """
    full = schema.model_json_schema()
    # Field schemas only, never the root envelope: shown the whole dump, the
    # model copies the envelope's own `description` key into its reply. Per-field
    # descriptions live inside `properties` and survive the strip.
    fields = {
        "properties": full.get("properties", {}),
        "required": full.get("required", []),
        **({"$defs": full["$defs"]} if "$defs" in full else {}),
    }
    keys = ", ".join(f"`{f}`" for f in schema.model_fields)
    return (
        f"Return a single json object whose top-level keys are exactly: {keys}. "
        "Emit no prose, no markdown fences — the json object only.\n\n"
        f"{json.dumps(fields, indent=2)}"
    )


def structured_messages(*, content_type: str, content: str, task: str) -> list[dict[str, str]]:
    """`[shared system, article envelope, task]` for one structured call.

    The first two messages are byte-identical across every task on the same
    item, so whichever runs second onwards reads the article from cache. `task`
    is the only per-call part.
    """
    return [
        {"role": "system", "content": SHARED_SYSTEM},
        {"role": "user", "content": render_envelope(content_type=content_type, content=content)},
        {"role": "user", "content": task},
    ]


def effective_prompt_sha(role_prompt: str, schema: type) -> str:
    """Per-call staleness signal over everything static the model is shown.

    Hashing the prompt markdown alone would miss the shared system message, the
    article envelope and the generated schema, so adding a field to `TopicCard`
    or rewording the envelope would change what the model is asked for while
    every existing row still read as fresh. Per-item values stay out — they are
    data, and would make every row uniquely stale, which is why the envelope
    enters as its unfilled template rather than as a wrapped article.
    """
    return hashlib.sha256(
        "\n".join(
            (SHARED_SYSTEM, ARTICLE_ENVELOPE, role_prompt, schema_block(schema))
        ).encode()
    ).hexdigest()


def build_role_prompt(spec: TaskSpec, prompt_text: str, *, user_notes: str | None) -> str:
    """The prompt as the sha should see it: reader-notes fold included when it
    will be sent, the notes themselves excluded because they are per-item data."""
    if spec.name == "followups" and user_notes:
        return prompt_text + READER_THREADS_FOLD
    return prompt_text


async def run_tasks(
    plan: list[tuple[TaskSpec, str, str]],
    *,
    content: str,
    content_type: str,
    user_notes: str | None,
    model: str,
    api_key: str,
) -> list[TaskOutcome]:
    """Run each `(spec, prompt_label, prompt_text)` in the order given.

    Sequential, not concurrent: every task sends the same article prefix, and a
    task only reads that prefix from cache once an earlier one has finished
    writing it. Firing them together would pay for the article once per task.

    One task's failure never stops the others. A caller asking for four outputs
    would rather have three and a named error than a batch that spent its money
    and returned nothing — and it can retry precisely what failed.
    """
    client = AsyncOpenAI(api_key=api_key)
    try:
        return [
            await _run_one(
                client,
                spec,
                prompt_label,
                prompt_text,
                content=content,
                content_type=content_type,
                user_notes=user_notes,
                model=model,
            )
            for spec, prompt_label, prompt_text in plan
        ]
    finally:
        # Close inside the loop that opened it, or the underlying httpx sockets
        # leak — an async destructor cannot run on a loop that is already gone.
        await client.close()


async def _run_one(
    client: AsyncOpenAI,
    spec: TaskSpec,
    prompt_label: str,
    prompt_text: str,
    *,
    content: str,
    content_type: str,
    user_notes: str | None,
    model: str,
) -> TaskOutcome:
    role_prompt = build_role_prompt(spec, prompt_text, user_notes=user_notes)
    outcome = TaskOutcome(
        task=spec.name,
        prompt_label=prompt_label,
        prompt_sha256=effective_prompt_sha(role_prompt, spec.schema),
    )

    task = role_prompt
    if spec.name == "followups" and user_notes:
        task += "\n\n[reader's notes — NOT part of the source article]\n" + user_notes
    # Role prompt, notes and schema all ride in the tail — everything ahead of
    # it must match this call's siblings or the article leaves the cache.
    task += "\n\n" + schema_block(spec.schema)

    t0 = time.monotonic()
    correction = ""
    try:
        for attempt in range(_MAX_ATTEMPTS):
            response = await client.chat.completions.create(
                model=model,
                prompt_cache_key=EXTRACTION_CACHE_KEY,
                messages=structured_messages(
                    content_type=content_type, content=content, task=task + correction
                ),
                response_format={"type": "json_object"},
                **token_kwargs(model, MAX_TOKENS),
            )
            # Summed across attempts so a retry's cost is not invisible.
            outcome.tokens_in += response.usage.prompt_tokens
            outcome.tokens_out += response.usage.completion_tokens
            attempt_cached = _cached_tokens(response.usage)
            if attempt_cached is not None:
                outcome.cached_tokens = (outcome.cached_tokens or 0) + attempt_cached

            choice = response.choices[0]
            refusal = getattr(choice.message, "refusal", None)
            if refusal is not None:
                # A refusal has empty content, which would read as malformed json
                # and burn the retries on a parse error that never says why.
                # `is not None`, not truthiness: the contract is null-or-string.
                raise _Permanent(f"model refused — {refusal}")
            if choice.finish_reason == "length":
                # Before the empty-reply path: a reasoning model can spend the
                # whole budget thinking and return nothing, and retrying that
                # burns another budget before reporting the wrong fault.
                raise _Permanent(
                    f"hit the {MAX_TOKENS}-token completion ceiling on this "
                    f"{content_type} item; nothing was stored. The ceiling covers "
                    f"thinking tokens, so the {outcome.tokens_out} spent is not "
                    "the reply's length — raise it or shorten the prompt."
                )
            try:
                body = (choice.message.content or "").strip()
                if not body:
                    # Named, or the failed row reads "Expecting value: line 1
                    # column 1". Transient on this API, so it retries.
                    raise ValueError("the model returned an empty reply")
                outcome.payload = validate_strict(spec.schema, body)
                break
            # ValueError covers pydantic.ValidationError (a subclass), malformed
            # json, and the undeclared-field rejection.
            except ValueError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise _Permanent(
                        f"no reply matched the schema on any of {_MAX_ATTEMPTS} "
                        f"attempts. Last rejection: {exc}"
                    ) from exc
                # Appended to the tail, never the prefix — a correction written
                # ahead of the article would cost the cache on every retry. It
                # also supplies the between-attempt variation sampling would
                # normally give: gpt-5 models reject `temperature`.
                correction = (
                    f"\n\nYour previous reply was rejected by the schema:\n{exc}\n"
                    "Return corrected json."
                )
    except _Permanent as exc:
        outcome.error = str(exc)
        outcome.retryable = False
    except Exception as exc:  # noqa: BLE001 — one task's fault must not sink the batch
        logger.warning("extract task %s failed: %s: %.200s", spec.name, type(exc).__name__, exc)
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.retryable = _is_retryable(exc)
    outcome.duration_ms = (time.monotonic() - t0) * 1000
    return outcome


class _Permanent(Exception):
    """A failure re-running the same request cannot fix — a refusal, a blown
    token ceiling, or a model that missed the schema on every attempt."""


def _is_retryable(exc: BaseException) -> bool:
    """Classify an unexpected failure. Default: transient, so a caller retries
    rather than treating a network blip as a permanent extraction failure."""
    from openai import APIStatusError

    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or exc.status_code in (408, 429)
    return True


def _cached_tokens(usage: Any) -> int | None:
    """Defensive read — `prompt_tokens_details` may be absent on older response
    shapes or non-cached models. None means "not reported", which is a different
    fact from a reported zero."""
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return None
    return getattr(details, "cached_tokens", None)
