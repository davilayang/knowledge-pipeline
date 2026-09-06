"""Metadata extraction — one structured call over the fetched body.

Answers two things no deterministic source covers for the whole corpus: who made
a piece and who published it. Platform metadata is absent or wrong too often to
replace this call — a YouTube channel is not a person — and the body is enough
on its own: the platform byline is already in the fetched text on 71 of the 72
rows that have one, so the model gets the content and nothing else.

Runs first in the extraction lane and primes its shared cache prefix — the same
system message, article envelope and `response_format` that narrative,
topic_card and followups then read the body from. All four calls sit in one
cache partition; this one pays the write.
"""

import dataclasses

from domains.extraction.schemas import MetadataPayload

from workflows.extraction.shared_prefix import (
    schema_block,
    structured_messages,
    token_kwargs,
    validate_strict,
)
from workflows.extraction.three_call_openai import EXTRACTION_CACHE_KEY
from workflows.llm import LLMCall, generate_messages_with_usage

# The reply is a handful of short fields; this ceiling bounds a runaway list
# rather than fitting the output. Reasoning models spend it on thinking too,
# which is why `token_kwargs` pins them to their lowest effort.
METADATA_MAX_TOKENS = 2048

# JSON mode guarantees valid json, not a reply that satisfies the schema, and
# the usual miss is one wrong field name — naming the rejection back to the model
# fixes it. The SDK's retries cannot: the HTTP call succeeded, the local
# validation failed. Three, like the sibling structured calls.
_MAX_ATTEMPTS = 3


def extract_metadata(
    content: str,
    *,
    content_type: str,
    prompt: str,
    model: str,
) -> tuple[MetadataPayload, LLMCall]:
    """Read `content` once and return the validated payload plus call usage.

    Raises ValueError on any reply the schema rejects, and on one cut off at the
    token ceiling — truncated JSON still parses, so the stop reason is the only
    sign it was cut. The caller decides what a failure means."""
    task = prompt + "\n\n" + schema_block(MetadataPayload)

    tokens_in = tokens_out = cached = 0
    correction = ""
    for attempt in range(_MAX_ATTEMPTS):
        call = generate_messages_with_usage(
            structured_messages(content_type=content_type, content=content, task=task + correction),
            model=model,
            prompt_cache_key=EXTRACTION_CACHE_KEY,
            response_format={"type": "json_object"},
            **token_kwargs(model, METADATA_MAX_TOKENS),
        )
        # Summed across attempts so a retry's cost is not invisible.
        tokens_in += call.input_tokens
        tokens_out += call.output_tokens
        cached += call.cached_tokens
        if call.finish_reason == "length":
            # Not retried: re-asking under the same ceiling truncates again.
            raise ValueError(
                f"metadata reply truncated at max_tokens={METADATA_MAX_TOKENS} "
                f"({call.output_tokens} completion tokens spent)"
            )
        try:
            payload = validate_strict(MetadataPayload, call.content)
            break
        # ValueError covers pydantic.ValidationError (a subclass), malformed
        # json, and the undeclared-field rejection.
        except ValueError as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            # Appended to the tail, never the prefix — a correction written
            # ahead of the article would cost the body cache on every retry.
            correction = (
                f"\n\nYour previous reply was rejected by the schema:\n{exc}\n"
                "Return corrected json."
            )
    return payload, dataclasses.replace(
        call, input_tokens=tokens_in, output_tokens=tokens_out, cached_tokens=cached
    )
