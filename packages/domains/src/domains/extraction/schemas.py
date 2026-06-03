"""Pydantic schemas for the three-call extraction payload.

DUPLICATED FROM newsletter-assistant's `packages/core/src/core/extraction_schemas.py`.
Cross-repo contract — changes must land in lockstep across both repos with a
coordinated PR + CHANGELOG entry in each. There is no shared workspace; drift
is caught by:

1. The round-trip JSON-fixture test in both repos
   (`tests/domains/extraction/test_schema_drift.py` here; equivalent on NA side).
2. NA's read-path pydantic validation failing loudly on missing fields.
3. Coordinated CHANGELOG entries on every field change.

When updating: record the NA commit hash you mirrored from in this docstring
and in the corresponding CHANGELOG entry.

Last sync: <pending NA-side ship — kp leads with the duplicated shape>
"""

from pydantic import BaseModel, Field


class TopicCard(BaseModel):
    """Structured analysis of one content item.

    Persisted as one row in `extraction_calls` with `call_kind='topic_card'`
    and `output` = `model_dump_json()`. Consumed by NA's `_KpQueueHandler` →
    `raw_store.apply_topic_diff()`."""

    extracted_title: str = Field(
        min_length=1,
        description="Short title — used as the row title in NA's content list.",
    )
    core_mechanism: str = Field(
        min_length=1,
        description=(
            "Central method/algorithm/system. One sentence, "
            "NAMED-METHOD does VERB to produce OUTCOME shape."
        ),
    )
    best_example: str = Field(
        min_length=1,
        description="Single most-specific named example. Names an org, person, or specific number.",
    )
    second_example: str | None = Field(
        default=None,
        description="Optional second example; differs along at least one axis from best_example.",
    )
    transferable_pattern: str = Field(
        min_length=1,
        description=(
            "Move the listener could apply outside this source's domain. "
            "Empty if it collapses to core_mechanism."
        ),
    )
    main_tension: str = Field(
        min_length=1,
        description="One trade-off, disagreement, or unresolved question from the source.",
    )
    candidate_tie_backs: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 4 attributed-concrete hooks — named paper/person/org with specific position."
        ),
    )


class Followups(BaseModel):
    """4–6 follow-up questions a curious reader would ask after the Topic Card.

    Persisted as one row in `extraction_calls` with `call_kind='followups'`
    and `output` = `model_dump_json()`. Surfaced as chip suggestions on the
    voice agent's drilldown turn."""

    questions: list[str] = Field(
        min_length=4,
        max_length=6,
        description=(
            "Complete English sentences ending in `?`. Answerable from the "
            "source content (not general world knowledge). Push toward "
            "mechanism, specifics, gaps, tradeoffs, or comparisons — never "
            "restate Topic Card fields."
        ),
    )


class ExtractionPayload(BaseModel):
    """View-level composition of the three extraction call outputs.

    Reconstructed at read time from three `extraction_calls` rows; never
    serialised to disk as a single blob. Callers compose this from the
    latest output per `call_kind`. NA's `_KpQueueHandler.extract_override`
    returns this model."""

    narrative_md: str = Field(
        min_length=1,
        description=(
            "Unstructured markdown — narrative call's output. "
            "What the LLM call-site sees as the tool result."
        ),
    )
    topic_card: TopicCard
    followups: Followups
