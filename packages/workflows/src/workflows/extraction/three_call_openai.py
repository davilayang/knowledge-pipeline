"""Three-call OpenAI extractor (v2 default).

Replaces the single-shot extractor with three focused calls:

1. **narrative** — unstructured markdown, no `response_format`.
2. **topic_card** — JSON mode, validated against `TopicCard`.
3. **followups** — JSON mode, validated against `Followups`.

Calls 2 and 3 run in sequence so 3 reads the article from the cache 2 writes.
Both conditions are load-bearing: they share one `response_format` value (OpenAI
partitions the prefix cache by it, which is why two pydantic schemas never
could), and `shared_prefix.structured_messages` keeps everything ahead of the
task tail byte-identical. Call 1 has no response format, so it caches alone.

Returns the composed `ExtractionPayload` (in-memory) + a list of
`ExtractionCallRecord` (one per call) — the writer in queue_store turns
those into one INSERT per row in `extraction_calls`.

`asyncio.run` inside a Dagster op is safe: ops execute in their own threads,
not on the daemon event loop. Sandbox-materialise once under the pinned
Dagster version before deploy.

LangGraph migration is a one-class swap: a future `LangGraphExtractor`
returns the same `(ExtractionPayload, list[ExtractionCallRecord])` shape
with extra rows for planner / critic nodes and `node_metadata` populated.
The asset code and the storage shape don't change.

Content-shape routing: `extract(...)` picks `prompt_sets[content_shape]`,
falling back to `prompt_sets["unknown"]` when no shape-specific bundle is
registered. `bundle_sha256(content_shape)` returns the staleness signal
for the SELECTED bundle so adding a new shape's prompts does not
invalidate prior shapes' rows.
"""

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import openai
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard

from workflows.extraction.shared_prefix import (
    effective_prompt_sha,
    schema_block,
    structured_messages,
    validate_strict,
)
from workflows.extraction.types import PromptBundle

_log = logging.getLogger(__name__)

_GENERIC_SHAPE = "unknown"

# Routing hint on all three calls: OpenAI steers requests sharing a key to the
# same cache, so the structured pair's shared article prefix lands on one machine.
EXTRACTION_CACHE_KEY = "kp-extraction"

# JSON mode guarantees syntactically valid json, not a reply that satisfies the
# pydantic model, so a structured call validates its own output and re-asks on
# failure. Three attempts: gpt-5-mini validated 16/16 in measurement, so this is
# insurance rather than the main path, and a model that has missed the schema
# twice is unlikely to find it on a fourth try.
_MAX_STRUCTURED_ATTEMPTS = 3

# The narrative call intermittently returns an empty completion — `finish_reason`
# still "stop", no refusal, nothing near the token ceiling, and the same content
# succeeds on a rerun. Transient rather than content-specific, so one retry
# clears most of them.
_MAX_NARRATIVE_ATTEMPTS = 2

# Appended to the followups task tail only when the caller supplies user_notes.
_READER_THREADS_FOLD = (
    "\n\n---\n"
    "The user message may include a `[reader's notes — NOT part of the source "
    "article]` block: the reader's own annotations, NOT source content. Populate "
    "`reader_threads` with each note restated as the reader's own thread (a focus "
    "they asked for, an open-loop/action, or context they gave). Never answer reader "
    "notes from the source, never invent threads, and never treat a note as a fact "
    "stated by the source. Leave `reader_threads` empty if the block is absent."
)


def _token_kwargs(model: str, max_tokens: int) -> dict[str, Any]:
    """Token-budget kwargs for the three calls, per model family.

    gpt-5-family are reasoning models: they reject `max_tokens` and need
    `max_completion_tokens`, plus the lowest reasoning effort — extraction wants
    coverage of the source, not deliberation. gpt-4.1/4o keep the classic
    `max_tokens` and reject `reasoning_effort` entirely.

    The two gpt-5 generations spell "lowest" differently and reject each other's
    value, so one prefix would 400 a whole generation on every call. Verified
    live: `gpt-5`/`gpt-5-mini` take `minimal`; `gpt-5.4-*` and `gpt-5.6-*` take
    `none`. Splitting on the dot means an unknown dotted release gets the newer
    spelling and fails safe. `gpt-5-chat-*` would be mis-routed; none is in use.
    """
    if model.startswith("gpt-5"):
        effort = "none" if model.startswith("gpt-5.") else "minimal"
        return {"max_completion_tokens": max_tokens, "reasoning_effort": effort}
    return {"max_tokens": max_tokens}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _cached_tokens(usage: Any) -> int | None:
    """Defensive read — OpenAI's `prompt_tokens_details` may be absent on
    older response shapes or non-cached models. Returns None when unavailable."""
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return None
    return getattr(details, "cached_tokens", None)


# (prompt_text, prompt_label, prompt_sha256) — internal expansion of one role
# of a PromptBundle. Kept as a 3-tuple so the per-call record-building path
# below stays a single attribute read.
_RoleTriple = tuple[str, str, str]


def _expand(bundle: PromptBundle) -> dict[str, _RoleTriple]:
    return {
        "narrative": (bundle.narrative[0], bundle.narrative[1], _sha(bundle.narrative[0])),
        "topic_card": (bundle.topic_card[0], bundle.topic_card[1], _sha(bundle.topic_card[0])),
        "followups": (bundle.followups[0], bundle.followups[1], _sha(bundle.followups[0])),
    }


class ThreeCallOpenAIExtractor:
    """v2 strategy. Three sequential OpenAI calls per content item. Returns
    (ExtractionPayload, list[ExtractionCallRecord]).

    Single-use: `.extract()` closes the underlying AsyncOpenAI client at the
    end of the call (same event loop that opened it, before asyncio.run
    destroys it). Re-calling `.extract()` on the same instance will fail
    against the closed httpx pool. ExtractorRegistry constructs a fresh
    instance per asset materialization via `.build()` — that pattern keeps
    socket lifecycles bounded to one Dagster op."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_sets: dict[str, PromptBundle],
        max_tokens: int = 2048,
    ):
        if _GENERIC_SHAPE not in prompt_sets:
            raise ValueError(
                f"prompt_sets must include an '{_GENERIC_SHAPE}' bundle — it's "
                "the generic fallback for content_shape values without a "
                "shape-specific set."
            )
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model
        # Pre-compute the per-role (text, label, sha) triple per shape. Sha
        # is the per-call staleness signal recorded on each
        # `ExtractionCallRecord`; computing here keeps the record-build path
        # branch-free.
        self._prompt_sets: dict[str, dict[str, _RoleTriple]] = {
            shape: _expand(bundle) for shape, bundle in prompt_sets.items()
        }
        self._max_tokens = max_tokens
        self._token_kwargs = _token_kwargs(model, max_tokens)

    @property
    def model(self) -> str:
        return self._model

    @property
    def bundle_label(self) -> str:
        """Cohort label written to queue_items.extractor_label. Bumped from
        `3call_v1` to `3call_v2_shape_routed` when the routing semantics
        changed — existing rows surface as stale via the re-extract sensor
        cohort comparison regardless of whether their resolved bundle text
        is byte-identical to today's `unknown` bundle."""
        return "3call_v2_shape_routed"

    def bundle_sha256(self, content_shape: str) -> str:
        """Cohort staleness signal for the SELECTED bundle, written to
        `queue_items.extractor_sha256`.

        Covers everything static the three calls send: the model, the narrative
        prompt, and — via `effective_prompt_sha` — the shared system message,
        each structured role prompt, and the schema generated from each pydantic
        model. Hashing the prompt markdown alone would leave every existing row
        reading as fresh after an edit to the shared system or a field added to
        `TopicCard`, even though both change what the model is asked for.

        The reader-notes fold is folded into the followups leg unconditionally.
        That over-fires slightly — editing it marks even note-free rows stale —
        which is the safe direction for a staleness signal.

        Pure function of the chosen bundle: adding a new shape's bundle does NOT
        invalidate prior shapes' rows. Falls back to the `unknown` bundle when
        the shape is not in the registered set."""
        bundle = self._prompt_sets.get(content_shape) or self._prompt_sets[_GENERIC_SHAPE]
        return _sha(
            "\n".join(
                (
                    self._model,
                    bundle["narrative"][0],
                    effective_prompt_sha(bundle["topic_card"][0], TopicCard),
                    effective_prompt_sha(bundle["followups"][0] + _READER_THREADS_FOLD, Followups),
                )
            )
        )

    def extract(
        self,
        content: str,
        *,
        content_type: str,
        content_shape: str,
        user_notes: str | None = None,
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        """Sync wrapper. Dagster ops run in their own threads, so asyncio.run
        does not collide with the daemon's event loop."""
        return asyncio.run(
            self._extract_async(
                content=content,
                content_type=content_type,
                content_shape=content_shape,
                user_notes=user_notes,
            )
        )

    async def _extract_async(
        self,
        *,
        content: str,
        content_type: str,
        content_shape: str,
        user_notes: str | None = None,
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        # Record the shape that actually drove the bundle selection — when
        # the caller passes an unregistered shape (e.g. a future
        # `research_summary` row hitting an older deploy), this resolves to
        # `unknown` so the per-row provenance matches the bundle that ran.
        resolved_shape = content_shape if content_shape in self._prompt_sets else _GENERIC_SHAPE
        bundle = self._prompt_sets[resolved_shape]
        try:
            narrative_record = await self._narrative_call(
                content, content_type, bundle["narrative"], resolved_shape
            )

            # Sequential, not concurrent: `followups` reads the article body from
            # the prompt cache that `topic_card` writes, which cannot happen while
            # the two are in flight together.
            topic_card, topic_record = await self._structured_call(
                content,
                content_type,
                bundle["topic_card"],
                TopicCard,
                "topic_card",
                resolved_shape,
            )
            followups, followups_record = await self._structured_call(
                content,
                content_type,
                bundle["followups"],
                Followups,
                "followups",
                resolved_shape,
                user_notes=user_notes or None,
            )

            payload = ExtractionPayload(
                narrative_md=narrative_record.output,
                topic_card=topic_card,
                followups=followups,
            )
            return payload, [narrative_record, topic_record, followups_record]
        finally:
            # Close the AsyncOpenAI client inside the same event loop that
            # opened it. asyncio.run() destroys the loop on return; if we
            # don't close here, the underlying httpx.AsyncClient sockets
            # leak (Python's async-destructor can't fire on a dead loop).
            # Extractor is single-use as a result — see class docstring.
            await self._client.close()

    async def _narrative_call(
        self,
        content: str,
        content_type: str,
        prompt_triple: _RoleTriple,
        resolved_shape: str,
    ) -> ExtractionCallRecord:
        prompt_text, prompt_label, prompt_sha = prompt_triple
        t0 = time.monotonic()
        tokens_in = tokens_out = 0
        cached: int | None = None
        for attempt in range(_MAX_NARRATIVE_ATTEMPTS):
            resp = await self._client.chat.completions.create(
                model=self._model,
                prompt_cache_key=EXTRACTION_CACHE_KEY,
                **self._token_kwargs,
                messages=[
                    {"role": "system", "content": prompt_text},
                    {
                        "role": "user",
                        "content": f"[content_type: {content_type}]\n\n{content}",
                    },
                ],
            )
            # Summed across attempts so a retry's cost is not invisible.
            tokens_in += resp.usage.prompt_tokens
            tokens_out += resp.usage.completion_tokens
            attempt_cached = _cached_tokens(resp.usage)
            if attempt_cached is not None:
                cached = (cached or 0) + attempt_cached
            refusal = getattr(resp.choices[0].message, "refusal", None)
            if refusal is not None:
                # A refusal also arrives as empty content — without this it
                # retries, then reports "empty narrative", which is just wrong.
                # `is not None`, not truthiness: the contract is null-or-string.
                raise RuntimeError(f"narrative: model refused — {refusal}")
            if resp.choices[0].finish_reason == "length":
                # Checked before the empty test below, so a reply cut off at zero
                # bytes is reported as truncation rather than retried as an empty
                # one — a retry under an unchanged ceiling truncates again.
                raise RuntimeError(
                    f"The narrative hit the {self._max_tokens}-token output ceiling "
                    f"and was cut off part-way through this {content_type} item "
                    f"({len(content):,} chars, {resp.usage.completion_tokens} tokens "
                    "produced). It was not stored: a cut-off narrative still reads as "
                    "a finished one, so the voice agent would speak it as complete. "
                    "The topic card and follow-ups were not attempted — the narrative "
                    "runs first. Retrying hits the same ceiling; shorten the source or "
                    "raise the extractor's max_tokens."
                )
            output = (resp.choices[0].message.content or "").strip()
            if output:
                break
            # Nothing is persisted when the call ultimately raises — the writer
            # only runs on a returned record — so this is the sole surviving
            # evidence of an empty attempt. Keep it until the cause is known.
            _log.warning(
                "empty narrative: attempt=%d/%d content_type=%s chars=%d "
                "finish_reason=%s tokens_in=%d tokens_out=%d",
                attempt + 1,
                _MAX_NARRATIVE_ATTEMPTS,
                content_type,
                len(content),
                resp.choices[0].finish_reason,
                resp.usage.prompt_tokens,
                resp.usage.completion_tokens,
            )
        else:
            # Written for whoever reads the Notion row: a run-failure sensor
            # copies the innermost exception message into that row's Error field,
            # where pydantic's `narrative_md` complaint named our data model
            # rather than the failure.
            raise RuntimeError(
                f"OpenAI returned an empty narrative on all {_MAX_NARRATIVE_ATTEMPTS} "
                f"attempts for this {content_type} item ({len(content):,} chars). "
                "The topic card and follow-ups were not attempted — the narrative "
                "runs first — so nothing else was spent on this item. The cause is "
                "not yet known; a retry has cleared it before. Retry by setting "
                "Status back to Fetching."
            )
        duration_ms = (time.monotonic() - t0) * 1000
        return ExtractionCallRecord(
            call_kind="narrative",
            prompt_label=prompt_label,
            prompt_sha256=prompt_sha,
            schema_name=None,
            output=output,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached,
            duration_ms=duration_ms,
            extracted_at=_now_iso(),
            prompt_set_shape=resolved_shape,
        )

    async def _structured_call(
        self,
        content: str,
        content_type: str,
        prompt_triple: _RoleTriple,
        schema: type,
        call_kind: str,
        resolved_shape: str,
        *,
        user_notes: str | None = None,
    ) -> tuple[Any, ExtractionCallRecord]:
        prompt_text, prompt_label, _ = prompt_triple
        role_prompt = prompt_text
        if user_notes:
            # The fold is prompt, so it joins the sha; the notes it refers to
            # are per-item data and stay out.
            role_prompt = prompt_text + _READER_THREADS_FOLD
        prompt_sha = effective_prompt_sha(role_prompt, schema)
        task = role_prompt
        if user_notes:
            task += "\n\n[reader's notes — NOT part of the source article]\n" + user_notes
        # Role prompt, notes and schema all ride in the tail — everything ahead
        # of it must match this call's sibling or the article leaves the cache.
        task += "\n\n" + schema_block(schema)
        t0 = time.monotonic()
        tokens_in = tokens_out = 0
        # None = the API reported no cache details, a different fact from a
        # reported zero. The narrative call and the ledger both keep it nullable.
        cached: int | None = None
        correction = ""
        for attempt in range(_MAX_STRUCTURED_ATTEMPTS):
            resp = await self._client.chat.completions.create(
                model=self._model,
                prompt_cache_key=EXTRACTION_CACHE_KEY,
                **self._token_kwargs,
                messages=structured_messages(
                    content_type=content_type, content=content, task=task + correction
                ),
                response_format={"type": "json_object"},
            )
            # Summed across attempts so a retry's cost is not invisible.
            tokens_in += resp.usage.prompt_tokens
            tokens_out += resp.usage.completion_tokens
            attempt_cached = _cached_tokens(resp.usage)
            if attempt_cached is not None:
                cached = (cached or 0) + attempt_cached
            refusal = getattr(resp.choices[0].message, "refusal", None)
            if refusal is not None:
                # A refusal has empty content, which would read as malformed
                # JSON and burn the retries on a parse error that never says why.
                # `is not None`, not truthiness: the contract is null-or-string.
                raise RuntimeError(f"{call_kind}: model refused — {refusal}")
            if resp.choices[0].finish_reason == "length":
                # Re-asking under the same ceiling truncates again. On gpt-5 the
                # ceiling covers reasoning tokens too, not just the reply.
                raise RuntimeError(
                    f"{call_kind}: reply truncated at max_tokens={self._max_tokens} "
                    f"(attempt {attempt + 1}) — raise the ceiling or shorten the schema"
                )
            try:
                parsed = validate_strict(schema, resp.choices[0].message.content or "")
                break
            # ValueError covers pydantic.ValidationError (a subclass), malformed
            # JSON, and the undeclared-field rejection.
            except ValueError as exc:
                if attempt == _MAX_STRUCTURED_ATTEMPTS - 1:
                    raise
                # Appended to the tail, never the prefix — a correction written
                # ahead of the article body would cost the cache on every retry.
                # It also supplies the between-attempt variation that sampling
                # would normally give: gpt-5 models reject `temperature`.
                correction = (
                    f"\n\nYour previous reply was rejected by the schema:\n{exc}\n"
                    "Return corrected json."
                )
        duration_ms = (time.monotonic() - t0) * 1000
        record = ExtractionCallRecord(
            call_kind=call_kind,
            prompt_label=prompt_label,
            prompt_sha256=prompt_sha,
            schema_name=schema.__name__,
            output=parsed.model_dump_json(),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached,
            duration_ms=duration_ms,
            extracted_at=_now_iso(),
            prompt_set_shape=resolved_shape,
        )
        return parsed, record
