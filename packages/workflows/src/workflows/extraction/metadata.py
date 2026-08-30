"""Metadata extraction — one structured call over the fetched body.

Answers four things no deterministic source covers for the whole corpus: who
made a piece, who published it, whether it needs a non-default spoken opening,
and whether its substance survived the fetch.

The body is the only substrate with no coverage hole — publisher metadata is
absent or wrong often enough (a YouTube channel is not a person; a platform
byline is not the guest author who wrote the piece) that deterministic sources
can only prime or cross-check this call, never replace it. What they do supply
is passed in as `evidence` for the model to reconcile against what it reads.

Rides the extraction lane's shared cache prefix (same system message, same
article envelope, same `response_format`), so on an item whose reading card is
also being extracted this call is served the body from cache. Everything
per-item lives in the task tail, behind the article.
"""

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

# The reply is a handful of short fields — this ceiling exists to bound a
# runaway list, not to fit the output. Reasoning models spend it on thinking
# too, which is why `token_kwargs` also pins them to their lowest effort.
METADATA_MAX_TOKENS = 2048


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

    The severity test: remove the unshown material — does a claim become
    unverifiable, or a section become empty? Then `major`. A pointing gesture
    attached to something also said aloud is `minor`."""

    cause: Literal["screen_reference", "images", "chrome", "truncation", "unspeakable"] = Field(
        description=(
            "screen_reference: points at something on screen. images: the text "
            "refers to figures not captured. chrome: navigation/boilerplate "
            "replaced the content. truncation: the text stops mid-thought. "
            "unspeakable: present but unreadable aloud, e.g. raw tables."
        )
    )
    severity: Literal["major", "minor"] = Field(
        description="major when a claim becomes unverifiable or a section empty."
    )
    missing: str = Field(description="What is not in the text, specifically.")
    evidence: str = Field(description="The quote from the text that depends on it.")


class MetadataPayload(BaseModel):
    """What one metadata call returns. Stored across the three `queue_items`
    metadata columns; the whole reply also lands in the `extraction_calls`
    ledger."""

    contributors: list[Contributor] = Field(
        description="People who made this, in the order the source presents them. Empty if none."
    )
    publisher: str | None = Field(
        default=None,
        description="Channel, site, show or org that published it. Null if unclear.",
    )
    delivery_shape: Literal["different_subjects", "different_goals"] | None = Field(
        default=None,
        description=(
            "different_subjects when the item bundles pieces sharing no subject "
            "(a digest issue covering an export ban, a funding round and a paper). "
            "different_goals when its sections serve different reader intentions "
            "(a README: what this is / how to install / how to configure). Null "
            "otherwise — null is the normal answer and fits most content."
        ),
    )
    parts: list[str] = Field(
        description="Names of the sections or items, when delivery_shape is set. Else empty."
    )
    unreadable: list[Unreadable] = Field(
        description="Substance the text references but does not contain. Empty when none."
    )


def extract_metadata(
    content: str,
    *,
    content_type: str,
    evidence: str,
    prompt: str,
    model: str,
) -> tuple[MetadataPayload, LLMCall]:
    """Read `content` once and return the validated payload plus call usage.

    Raises ValueError on any reply the schema does not accept, and on a reply cut
    off at the token ceiling — truncated JSON can still parse, so the stop reason
    is the only evidence that a half-read body produced it. The caller decides
    what a failure means; here it is always "this reply is not usable"."""
    task = prompt
    if evidence:
        # Per-item, so it goes in the tail: anything ahead of the article that
        # differs between calls voids the body's shared prefix cache.
        task += f"\n\n[what other sources say about this item]\n{evidence}"
    task += "\n\n" + schema_block(MetadataPayload)

    call = generate_messages_with_usage(
        structured_messages(content_type=content_type, content=content, task=task),
        model=model,
        prompt_cache_key=EXTRACTION_CACHE_KEY,
        response_format={"type": "json_object"},
        **token_kwargs(model, METADATA_MAX_TOKENS),
    )
    if call.finish_reason == "length":
        raise ValueError(
            f"metadata reply truncated at max_tokens={METADATA_MAX_TOKENS} "
            f"({call.output_tokens} completion tokens spent)"
        )
    return validate_strict(MetadataPayload, call.content), call
