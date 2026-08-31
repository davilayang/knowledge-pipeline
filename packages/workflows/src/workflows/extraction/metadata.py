"""Metadata extraction — one structured call over the fetched body.

Answers two things no deterministic source covers for the whole corpus: who made
a piece and who published it. Platform metadata is absent or wrong too often to
replace this call — a YouTube channel is not a person — and the body is enough
on its own: the platform byline is already in the fetched text on 71 of the 72
rows that have one, so the model gets the content and nothing else.

Rides the extraction lane's shared cache prefix (same system message, same
article envelope, same `response_format`), priming the body cache that
topic_card and followups then read. The narrative call sends a different system
message and no `response_format`, so it sits in another partition and is not
primed.
"""

import dataclasses
from typing import Literal

from pydantic import BaseModel, Field

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


class Contributor(BaseModel):
    """One person who made the content. Never an organisation — the channel,
    site, or show is the publisher, which is a separate field."""

    name: str = Field(description="The person's name as the source gives it.")
    role: str | None = Field(
        default=None,
        description=(
            "How they contributed: presenter, guest, host, author, maintainer, "
            "poster. Null when the source does not say."
        ),
    )
    affiliation: str | None = Field(
        default=None,
        description="Org they are stated to belong to. Null when unstated.",
    )


class Unreadable(BaseModel):
    """One piece of substance the fetched text references but does not contain.

    These descriptions are appended to the prompt and are the last thing the
    model reads before answering, so they must agree with it. An earlier version
    described severity as "does a claim become unverifiable" — the reading that
    called 41% of production bodies major, against 1.8% for the refetch reading
    the prompt now uses. Stating the superseded test here reinstated it from the
    strongest position in the message."""

    cause: Literal["screen_reference", "images", "chrome", "truncation", "unspeakable"] = Field(
        description=(
            "Why the material is missing. chrome: site furniture, a wall or an "
            "error page stands where the content should be. truncation: the "
            "content is cut — stops mid-thought, an elided span, an announced "
            "section left empty, a stub of a longer piece. Those two mean the "
            "text arrived damaged. The rest never were text and no refetch "
            "recovers them: screen_reference points at something on screen, "
            "images refers to figures not captured, unspeakable is present but "
            "cannot be read aloud, e.g. a raw table."
        )
    )
    severity: Literal["major", "minor"] = Field(
        description=(
            "major only when a refetch, or a better source, would recover the "
            "missing material — the text arrived broken. minor when the material "
            "was never text, however central it is to the piece. A claim you "
            "cannot verify from the text alone is not by itself major."
        )
    )
    missing: str = Field(description="What is not in the text, specifically.")
    evidence: str = Field(
        description=(
            "A quote from the text. For screen_reference, images and unspeakable, "
            "the line that depends on the unshown material; for chrome and "
            "truncation, the damage itself — the wall or error text, or the last "
            "words before the text stops."
        )
    )


class MetadataPayload(BaseModel):
    """What one metadata call returns. Stored across the two `queue_items`
    metadata columns; the whole reply also lands in the `extraction_calls`
    ledger."""

    contributors: list[Contributor] = Field(
        # Defaulted: omitting an empty list rather than sending `[]` is cosmetic,
        # and rejecting the payload over it would throw the publisher away too.
        default_factory=list,
        description="People who made this, in the order the source presents them. Empty if none.",
    )
    publisher: str | None = Field(
        default=None,
        description="Channel, site, show or org that published it. Null if unclear.",
    )
    unreadable: list[Unreadable] = Field(
        default_factory=list,
        description=(
            "Substance the text points at but does not contain. Empty when the "
            "text stands on its own."
        ),
    )


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
