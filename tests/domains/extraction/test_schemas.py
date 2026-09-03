"""Tests for the duplicated extraction-payload schemas.

These mirror NA's `packages/core/src/core/extraction_schemas.py` field-for-field
(cross-repo contract — see domains/extraction/schemas.py docstring). Drift is
caught by `test_schema_drift.py` against a pinned JSON fixture.
"""

import pytest
from domains.extraction.schemas import ExtractionPayload, Followups, Narrative, TopicCard
from pydantic import ValidationError


def test_topic_card_roundtrip_preserves_all_fields():
    card = TopicCard(
        extracted_title="Trust Factory",
        core_mechanism=(
            "Kent Beck names PAIRWISE TRUST as the substrate "
            "that lets engineering teams move faster."
        ),
        best_example="A pair-programming session where one developer trusts the other to refactor.",
        second_example=None,
        transferable_pattern="Run a small reversible bet on a trust-extending move; repeat.",
        main_tension="Trust takes time to build but seconds to destroy.",
        candidate_tie_backs=["Ron Jeffries on TDD pair flow", "Linda Rising on retrospectives"],
    )
    rehydrated = TopicCard.model_validate_json(card.model_dump_json())
    assert rehydrated == card


def test_topic_card_rejects_empty_required_string():
    """Empty extracted_title is a contract violation — pydantic rejects."""
    with pytest.raises(ValidationError):
        TopicCard(
            extracted_title="",
            core_mechanism="x",
            best_example="x",
            transferable_pattern="x",
            main_tension="x",
        )


def test_topic_card_candidate_tie_backs_defaults_to_empty_list():
    card = TopicCard(
        extracted_title="t",
        core_mechanism="m",
        best_example="e",
        transferable_pattern="p",
        main_tension="x",
    )
    assert card.candidate_tie_backs == []


def test_followups_accepts_4_to_6_questions():
    Followups(questions=["a?", "b?", "c?", "d?"])
    Followups(questions=["a?", "b?", "c?", "d?", "e?", "f?"])


def test_followups_rejects_fewer_than_4_questions():
    with pytest.raises(ValidationError):
        Followups(questions=["a?", "b?", "c?"])


def test_followups_rejects_more_than_6_questions():
    with pytest.raises(ValidationError):
        Followups(questions=["a?", "b?", "c?", "d?", "e?", "f?", "g?"])


def test_extraction_payload_composes_three_calls():
    payload = ExtractionPayload(
        narrative_md="# Narrative\n\nBody.",
        topic_card=TopicCard(
            extracted_title="t",
            core_mechanism="m",
            best_example="e",
            transferable_pattern="p",
            main_tension="x",
        ),
        followups=Followups(questions=["a?", "b?", "c?", "d?"]),
    )
    rehydrated = ExtractionPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload
    assert rehydrated.narrative_md.startswith("# Narrative")
    assert rehydrated.topic_card.extracted_title == "t"
    assert len(rehydrated.followups.questions) == 4


_Q = ["a?", "b?", "c?", "d?"]


def test_followups_reader_threads_defaults_empty():
    assert Followups(questions=_Q).reader_threads == []


def test_followups_accepts_reader_threads():
    f = Followups(questions=_Q, reader_threads=["compare with dbt"])
    assert f.reader_threads == ["compare with dbt"]


def test_beats_may_not_outnumber_the_claims_they_are_selected_from():
    """The voice agent subtracts one rendered count from the other and speaks it.

    The beats are a selection from the claims, so more beats than claims is not
    a thin extraction, it is an impossible one — and the failure is not silent:
    newsletter-assistant's prompt turns the two counts into "that is five of the
    fifteen this piece rests on". Four beats over three claims makes the agent
    say "that is four of the three", confidently, into a channel where the
    listener has nothing to check it against.

    Deriving the counts from the arrays removed the arithmetic slip; it did not
    make the relationship between the two lists true, which is why this is
    enforced rather than assumed.
    """
    with pytest.raises(ValidationError, match="more delivery beats"):
        Narrative(
            speakers_and_author="Priya Raghunathan (Latchkey)",
            structure="one throughline - routing beats scale",
            core_idea="Measure the traffic first.",
            load_bearing_claims=["Claim one - anchor", "Claim two - anchor"],
            delivery_beats=["Beat one", "Beat two", "Beat three"],
            named_concepts_and_entities="Priya Raghunathan, Latchkey",
        )


def test_beats_stop_at_six_because_the_agent_walks_them_one_turn_each():
    """`delivery_beats` is what the voice agent speaks a turn at a time, so its
    length is a spoken-session budget rather than a formatting preference.

    Measured on the narrative-coverage gold, one run in thirty emitted eleven
    beats against the stated four-to-six, and the coverage metric scored that
    run 1.000 — recall over reference threads has no term for material the
    narrative adds, so nothing downstream could see it. The bound is deliberately
    one-sided: the prompt forbids inventing a beat to reach four, so a lower
    bound would enforce the padding it bans.
    """
    with pytest.raises(ValidationError):
        Narrative(
            speakers_and_author="Priya Raghunathan (Latchkey)",
            structure="one throughline - routing beats scale",
            core_idea="Measure the traffic first.",
            load_bearing_claims=[f"Claim {i} - anchor" for i in range(1, 9)],
            delivery_beats=[f"Beat {i}" for i in range(1, 8)],
            named_concepts_and_entities="Priya Raghunathan, Latchkey",
        )
