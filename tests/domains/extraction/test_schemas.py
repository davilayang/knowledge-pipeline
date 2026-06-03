"""Tests for the duplicated extraction-payload schemas.

These mirror NA's `packages/core/src/core/extraction_schemas.py` field-for-field
(cross-repo contract — see domains/extraction/schemas.py docstring). Drift is
caught by `test_schema_drift.py` against a pinned JSON fixture.
"""

import pytest
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard
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
