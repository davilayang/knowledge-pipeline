"""Running the extraction tasks against OpenAI.

Message layout, token kwargs, JSON mode, the cache-key hint and the decision to
serialise all live here, because each follows from how this vendor caches rather
than from what extraction is. Keeping them together makes a second provider a
new file plus a dispatch.
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

# JSON mode guarantees valid json, not a reply the pydantic model accepts, so
# each call validates its own output and re-asks. Three: a model that has missed
# the schema twice is unlikely to find it on a fourth try.
_MAX_ATTEMPTS = 3

# The widest narrative measured spends 2,131 of this, on the worst of three real
# long sources (70k-233k characters). Not a request field: a caller cannot know
# the ceiling a prompt needs, and an eval measuring a different one from
# production would report numbers production never produces.
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

    gpt-5 reject `max_tokens`, need `max_completion_tokens`, and take the lowest
    reasoning effort — extraction wants coverage, not deliberation. gpt-4.1/4o
    keep `max_tokens` and reject `reasoning_effort` outright. The two gpt-5
    generations spell "lowest" differently and reject each other's value, so
    splitting on the dot is what stops one spelling 400ing a whole generation:
    `gpt-5-mini` takes `minimal`, dotted `gpt-5.x` takes `none`.
    """
    if model.startswith("gpt-5"):
        effort = "none" if model.startswith("gpt-5.") else "minimal"
        return {"max_completion_tokens": max_tokens, "reasoning_effort": effort}
    return {"max_tokens": max_tokens}


def schema_block(schema: type) -> str:
    """The output contract appended to a call's task tail, generated from the
    pydantic model.

    JSON mode never shows the model a schema, so the prompt carries one — which
    also supplies the literal word "json" the API requires. It rides in the tail,
    not a strict `response_format`, because that is what keeps the article prefix
    identical across tasks.
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

    The prompt markdown alone would miss the system message, article envelope and
    generated schema, leaving stored rows reading as fresh after a field or a
    reword changed the question. Per-item values stay out or every row would be
    uniquely stale — hence the envelope's unfilled template, not a wrapped
    article.
    """
    return hashlib.sha256(
        "\n".join((SHARED_SYSTEM, ARTICLE_ENVELOPE, role_prompt, schema_block(schema))).encode()
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

    Sequential: a task reads the shared article prefix from cache only once an
    earlier one has written it, so firing them together pays for the article
    per task. One failure never stops the others — three outputs and a named
    error beat none, and the caller can retry exactly what failed.
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
        # Closed on the loop that opened it: an async destructor cannot run on a
        # dead loop, so the httpx sockets would leak.
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
    # Prompt, notes and schema all ride in the tail: anything ahead of it must
    # match this call's siblings or the article leaves the cache.
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
                # A refusal has empty content, so it would otherwise burn the
                # retries on a parse error that never says why. `is not None`,
                # not truthiness: the contract is null-or-string.
                raise _Permanent(f"model refused — {refusal}")
            if choice.finish_reason == "length":
                # Before the empty-reply path: a reasoning model can spend the
                # whole budget thinking and return nothing, and retrying that
                # buys a second wrong answer to the same question.
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
                    # column 1". Transient here, so it retries.
                    raise ValueError("the model returned an empty reply")
                outcome.payload = validate_strict(spec.schema, body)
                break
            # ValueError covers pydantic.ValidationError, malformed json, and
            # the undeclared-field rejection.
            except ValueError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise _Permanent(
                        f"no reply matched the schema on any of {_MAX_ATTEMPTS} "
                        f"attempts. Last rejection: {exc}"
                    ) from exc
                # Tail, never prefix: a correction ahead of the article would
                # cost the cache on every retry. It also supplies the variation
                # sampling would give, which gpt-5 denies by rejecting
                # `temperature`.
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
