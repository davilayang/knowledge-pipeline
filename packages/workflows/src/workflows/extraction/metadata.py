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


# Nested-model docstrings and field descriptions survive into the generated
# schema's `$defs`, which is appended after the prompt body — the last thing the
# model reads before answering. Two consequences, both learned the hard way.
# Nothing here may contradict the prompt: a revision once described a grading
# rule by a test the prompt had already superseded, which reinstated that test
# from the strongest position in the message and cost the calibration the prompt
# exists to apply. And nothing here should spend that position on repo history — a note
# explaining what a rule replaced is text the model must read and reconcile, so
# it belongs in a comment like this one rather than in a docstring.
class Unreadable(BaseModel):
    """One piece of substance the fetched text references but does not contain.

    Reported for every gap, whatever its size. Whether the piece survives its
    gaps is `MetadataPayload.stands_alone`, judged once over the whole text
    rather than graded per entry: a per-entry grade was measured drifting across
    repeat runs of the same body, and it asked about the fetch rather than about
    whether the result could be used."""

    cause: Literal["screen_reference", "images", "chrome", "truncation", "unspeakable"] = Field(
        description=(
            "Why the material is missing. chrome: site furniture, a wall or an "
            "error page stands where the content should be. truncation: the "
            "content is cut — stops mid-thought, an elided span, an announced "
            "section left empty, a stub of a longer piece. Those two mean the "
            "fetch went wrong and is worth retrying. The rest never were text, "
            "so no refetch recovers them: screen_reference points at something on screen, "
            "images refers to figures not captured, unspeakable is present but "
            "cannot be read aloud, e.g. a raw table."
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
            "text contains everything it refers to."
        ),
    )
    stands_alone: bool = Field(
        description=(
            "False only when this text does not carry enough of the piece's "
            "substance to stand on its own — the material it points at is "
            "absent and what remains does not hold up. A piece that references "
            "slides or figures it does not contain still stands alone when its "
            "argument comes through."
        )
    )
    stands_alone_reason: str = Field(
        default="",
        description=(
            "Required when `stands_alone` is false: one sentence naming what is "
            "absent and why the piece does not hold without it. Must refer to "
            "the evidence quote of one of the `unreadable` entries."
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
