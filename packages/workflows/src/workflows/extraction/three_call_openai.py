"""Three-call OpenAI extractor (v2 default).

Replaces the single-shot extractor with three focused calls:

1. **narrative** — unstructured markdown (`chat.completions.create`)
   primes the prompt cache by firing first.
2. **topic_card** — pydantic-structured `TopicCard`
   (`beta.chat.completions.parse`, response_format=TopicCard).
3. **followups** — pydantic-structured `Followups`
   (response_format=Followups).

Calls 2+3 fire in parallel via `asyncio.gather(..., return_exceptions=True)`;
both hit OpenAI's prompt prefix cache (TTL 5–10 min) within sub-seconds of
call 1. Returns the composed `ExtractionPayload` (in-memory) + a list of
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
import time
from datetime import UTC, datetime
from typing import Any

import openai
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard

from workflows.extraction.types import PromptBundle

_GENERIC_SHAPE = "unknown"


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
    """v2 strategy. Three OpenAI calls per content item, asyncio.gather for the
    structured pair. Returns (ExtractionPayload, list[ExtractionCallRecord]).

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
        """Hash across model + the three prompt texts of the SELECTED
        bundle. Pure function of the chosen bundle — adding a new shape's
        bundle does NOT invalidate prior shapes' rows. Falls back to the
        `unknown` bundle when the shape is not in the registered set."""
        bundle = self._prompt_sets.get(content_shape) or self._prompt_sets[_GENERIC_SHAPE]
        return _sha(
            "\n".join(
                (
                    self._model,
                    bundle["narrative"][0],
                    bundle["topic_card"][0],
                    bundle["followups"][0],
                )
            )
        )

    def extract(
        self, content: str, *, content_type: str, content_shape: str
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        """Sync wrapper. Dagster ops run in their own threads, so asyncio.run
        does not collide with the daemon's event loop."""
        return asyncio.run(
            self._extract_async(
                content=content,
                content_type=content_type,
                content_shape=content_shape,
            )
        )

    async def _extract_async(
        self, *, content: str, content_type: str, content_shape: str
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        bundle = self._prompt_sets.get(content_shape) or self._prompt_sets[_GENERIC_SHAPE]
        try:
            narrative_record = await self._narrative_call(
                content, content_type, bundle["narrative"]
            )

            topic_result, followups_result = await asyncio.gather(
                self._structured_call(
                    content,
                    content_type,
                    bundle["topic_card"],
                    TopicCard,
                    "topic_card",
                ),
                self._structured_call(
                    content,
                    content_type,
                    bundle["followups"],
                    Followups,
                    "followups",
                ),
                return_exceptions=True,
            )

            if isinstance(topic_result, BaseException):
                raise topic_result
            if isinstance(followups_result, BaseException):
                raise followups_result

            topic_card, topic_record = topic_result
            followups, followups_record = followups_result

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
        self, content: str, content_type: str, prompt_triple: _RoleTriple
    ) -> ExtractionCallRecord:
        prompt_text, prompt_label, prompt_sha = prompt_triple
        t0 = time.monotonic()
        resp = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": prompt_text},
                {
                    "role": "user",
                    "content": f"[content_type: {content_type}]\n\n{content}",
                },
            ],
        )
        duration_ms = (time.monotonic() - t0) * 1000
        return ExtractionCallRecord(
            call_kind="narrative",
            prompt_label=prompt_label,
            prompt_sha256=prompt_sha,
            schema_name=None,
            output=resp.choices[0].message.content or "",
            tokens_in=resp.usage.prompt_tokens,
            tokens_out=resp.usage.completion_tokens,
            cached_tokens=_cached_tokens(resp.usage),
            duration_ms=duration_ms,
            extracted_at=_now_iso(),
        )

    async def _structured_call(
        self,
        content: str,
        content_type: str,
        prompt_triple: _RoleTriple,
        schema: type,
        call_kind: str,
    ) -> tuple[Any, ExtractionCallRecord]:
        prompt_text, prompt_label, prompt_sha = prompt_triple
        t0 = time.monotonic()
        resp = await self._client.beta.chat.completions.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": prompt_text},
                {
                    "role": "user",
                    "content": f"[content_type: {content_type}]\n\n{content}",
                },
            ],
            response_format=schema,
        )
        duration_ms = (time.monotonic() - t0) * 1000
        parsed = resp.choices[0].message.parsed
        record = ExtractionCallRecord(
            call_kind=call_kind,
            prompt_label=prompt_label,
            prompt_sha256=prompt_sha,
            schema_name=schema.__name__,
            output=parsed.model_dump_json(),
            tokens_in=resp.usage.prompt_tokens,
            tokens_out=resp.usage.completion_tokens,
            cached_tokens=_cached_tokens(resp.usage),
            duration_ms=duration_ms,
            extracted_at=_now_iso(),
        )
        return parsed, record
