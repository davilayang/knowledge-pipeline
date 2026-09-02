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

`Narrative` is kp-only, and the three drift detectors above do NOT cover it.
There is no mirrored class on the NA side and deliberately so: NA renders the
stored json generically from its keys, precisely so a section can be added here
without a release there. `test_schema_drift.py` pins `ExtractionPayload`
fixtures, not this model, and NA's read path does not validate the narrative, so
detector 2 cannot fire for it.

What guards it instead is `tests/domains/extraction/test_render.py`, which pins
the two properties NA's generic renderer depends on: every field's `title`
equals what NA derives from the json key, and every field is a plain string or a
plain list. Break either and the two repos emit different text for one narrative.

Which sections may exist at all, what each one guarantees, and what the consumer
is obliged to do with it are not decided here — they are the narrative contract
in the `data-context-builder` hub, at
`documents/knowledge-os/contracts/narrative.md`. It requires every field on the
wire to carry a consumer decision, and puts that enforcement on this side, so
adding a field below means adding its row there first. Nothing fails if you skip
it: the field reaches a reading model that was told nothing about it.

NA tells the json rows from the older markdown ones by
`extraction_calls.schema_name`, null on every narrative row written before the
call became structured. NA's branch for that is already on its main.
"""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator


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
    reader_threads: list[str] = Field(
        default_factory=list,
        description=(
            "The reader's own threads, extracted ONLY from the "
            "[reader's notes] block in the user message — a focus they "
            "asked for, an open-loop/action, or context they gave. Empty "
            "when no reader notes are present. Never source claims; never "
            "invented; never treat a note as a fact stated by the source."
        ),
    )


# A blank field renders as a header with nothing under it, which the agent reads
# as a source with nothing to say rather than a failed extraction. `min_length`
# counts characters and "   " is three, so the strip is what means "not blank".
NarrativeProse = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Narrative(BaseModel):
    """The narrative call's sections, one field each.

    Persisted as one row in `extraction_calls` with `call_kind='narrative'`
    and `output` = `model_dump_json()`, matching `TopicCard` and `Followups`.
    Rendered back to headed text by `domains.extraction.render.render_narrative`
    for the voice agent, which reads prose rather than json.

    Each field's `title` is the header the renderer emits, and `schema_block()`
    dumps the titles into the prompt's task tail, so the model is asked for the
    same names the renderer writes. That coupling is why there is one class here
    rather than one per prompt version: the prompt body and this model generate
    each other's field list, so a prompt version and this shape have to change in
    the same commit. The superseded prompt file stays on disk as history and can
    no longer be run.

    Field order is generation order — the model writes top to bottom — and two
    positions are load-bearing. `structure` precedes `core_idea` so the shape is
    committed before any content is written; a mandatory first-position core idea
    was measured flattening distinct shapes into one, with four labels collapsing
    to two. `load_bearing_claims` precedes `delivery_beats` because the beats are
    selected from it."""

    speakers_and_author: NarrativeProse = Field(
        title="Speakers and author",
        description=(
            "Who produced this, by name, with affiliation. Guest or speaker "
            "first, then the host. Never a bare role in place of a name, and "
            "never a channel or publication; `the host` is allowed only "
            "alongside a named guest, when the source does not name the host. "
            "Exactly `not named in the source` if it names nobody."
        ),
    )
    structure: NarrativeProse = Field(
        title="Structure",
        description=(
            "The shape of the source. Exactly one of `one throughline`, "
            "`a sequence`, or `N independent threads` (with the number), then "
            "a dash and one sentence describing that shape. Description only — "
            "instructions placed here were measured being ignored."
        ),
    )
    core_idea: NarrativeProse = Field(
        title="Core idea",
        description=(
            "1-2 sentences, conditioned on `structure`. For a throughline or a "
            "sequence, the single thing worth knowing. For independent "
            "threads, what the set is OF — do not manufacture a thesis over a "
            "bundle."
        ),
    )
    load_bearing_claims: list[NarrativeProse] = Field(
        title="Load bearing claims",
        min_length=1,
        description=(
            "The complete set of claims the piece collapses without — not the "
            "main point, and not everything it says. Typically 9-28. One claim "
            "per entry, each carrying its own anchor: a figure, a named "
            "entity, a mechanism, or a short quote."
        ),
    )
    delivery_beats: list[NarrativeProse] = Field(
        title="Delivery beats",
        min_length=1,
        description=(
            "Usually 4-6 beats selected from `load_bearing_claims`, ordered "
            "for a listener hearing this cold — fewer when the source carries "
            "fewer distinct ideas, never padded to reach four. One idea each, "
            "chained so every beat "
            "reuses a name, term or figure from the one before. Each entry is "
            "the idea, then an `Anchor:` line, then a `bridge_to:` line naming "
            "what comes next — omitted on the last beat."
        ),
    )
    named_concepts_and_entities: NarrativeProse = Field(
        title="Named concepts and entities",
        description=(
            "One comma-separated string, not a list: it is read aloud as one "
            "phrase rather than walked as an inventory, and the renderer counts "
            "and numbers every list it is given. Named individuals first, then "
            "companies, products, techniques."
        ),
    )

    @model_validator(mode="after")
    def _beats_do_not_outnumber_claims(self) -> "Narrative":
        """The beats are a selection from the claims, so more beats than claims
        is an impossible reply rather than a thin one.

        This is the one relationship between the two lists that deriving their
        counts does not make true. It matters because the consumer subtracts one
        rendered count from the other and speaks the result: four beats over
        three claims becomes "that is four of the three", said confidently into a
        channel where the listener has nothing to check it against. Failing here
        costs a retry; not failing costs the user's trust in every count."""
        if len(self.delivery_beats) > len(self.load_bearing_claims):
            raise ValueError(
                f"more delivery beats ({len(self.delivery_beats)}) than "
                f"load-bearing claims ({len(self.load_bearing_claims)}); the beats "
                "are selected from the claims, so this reply cannot be right"
            )
        return self


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
