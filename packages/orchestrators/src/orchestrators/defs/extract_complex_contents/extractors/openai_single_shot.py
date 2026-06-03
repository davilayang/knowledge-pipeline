"""Single-shot OpenAI extractor (v1 default).

One OpenAI Chat Completions call with the per-type prompt, JSON-mode
response. Same provider as synthesize_wiki. The v5 prompt was originally
tuned against Claude in PR #65; the v1 OpenAI port may need light
re-tuning if Topic Card quality regresses.
"""

import json
import re
from typing import Any

import openai

from .protocol import ExtractionUsage

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_TOPIC_CARD_KEYS = (
    "extracted_title",
    "core_mechanism",
    "best_example",
    "second_example",
    "transferable_pattern",
    "main_tension",
    "candidate_tie_backs",
    "likely_follow_up_questions",
)


def _parse_topic_card(text: str) -> dict[str, Any]:
    """Parse the JSON block emitted by the v5 extraction prompt.

    Maps the prompt's `title` field to our schema's `extracted_title`. Drops
    any extra keys; passes through known Topic Card fields. Raises ValueError
    if no JSON object can be located — the asset turns that into dg.Failure."""
    match = _JSON_BLOCK_RE.search(text)
    payload = match.group(1) if match else text.strip()
    data = json.loads(payload)
    if "title" in data and "extracted_title" not in data:
        data["extracted_title"] = data.pop("title")
    return {k: data.get(k) for k in _TOPIC_CARD_KEYS}


class SingleShotOpenAIExtractor:
    def __init__(self, *, api_key: str, model: str, prompt_text: str, max_tokens: int = 2048):
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model
        self._prompt_text = prompt_text
        self._max_tokens = max_tokens

    def extract(self, content: str, *, content_type: str) -> tuple[dict[str, Any], ExtractionUsage]:
        resp = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": self._prompt_text},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
        )
        body_text = resp.choices[0].message.content or ""
        extraction = _parse_topic_card(body_text)
        usage = ExtractionUsage(
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
        )
        return extraction, usage
