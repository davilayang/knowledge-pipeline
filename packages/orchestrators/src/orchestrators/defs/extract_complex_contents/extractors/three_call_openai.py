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
"""

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from typing import Any

import openai
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard


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


class ThreeCallOpenAIExtractor:
    """v2 strategy. Three OpenAI calls per content item, asyncio.gather for the
    structured pair. Returns (ExtractionPayload, list[ExtractionCallRecord])."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        narrative_prompt: str,
        narrative_prompt_label: str,
        topic_card_prompt: str,
        topic_card_prompt_label: str,
        followups_prompt: str,
        followups_prompt_label: str,
        max_tokens: int = 2048,
    ):
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model
        self._narrative = (narrative_prompt, narrative_prompt_label, _sha(narrative_prompt))
        self._topic_card = (topic_card_prompt, topic_card_prompt_label, _sha(topic_card_prompt))
        self._followups = (followups_prompt, followups_prompt_label, _sha(followups_prompt))
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    @property
    def bundle_label(self) -> str:
        """Cohort label written to queue_items.extractor_label. Bumped manually
        when ANY of the three sub-prompts changes shape."""
        return "3call_v1"

    @property
    def bundle_sha256(self) -> str:
        """Hash across the three prompt texts — canonical staleness signal."""
        return _sha(self._narrative[0] + "\n" + self._topic_card[0] + "\n" + self._followups[0])

    def extract(
        self, content: str, *, content_type: str
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        """Sync wrapper. Dagster ops run in their own threads, so asyncio.run
        does not collide with the daemon's event loop."""
        return asyncio.run(self._extract_async(content=content, content_type=content_type))

    async def _extract_async(
        self, *, content: str, content_type: str
    ) -> tuple[ExtractionPayload, list[ExtractionCallRecord]]:
        narrative_record = await self._narrative_call(content, content_type)

        topic_result, followups_result = await asyncio.gather(
            self._structured_call(
                content,
                content_type,
                self._topic_card,
                TopicCard,
                "topic_card",
            ),
            self._structured_call(
                content,
                content_type,
                self._followups,
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

    async def _narrative_call(self, content: str, content_type: str) -> ExtractionCallRecord:
        prompt_text, prompt_label, prompt_sha = self._narrative
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
        prompt_triple: tuple[str, str, str],
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
