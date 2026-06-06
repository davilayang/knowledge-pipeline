"""Tests for evals.extraction.types — Step 3 fixture + diff record shapes."""

import pytest
from evals.extraction.types import (
    ExtractionDiffReport,
    ExtractionFixture,
    TopicCardFields,
)


def test_extraction_fixture_is_frozen_dataclass():
    f = ExtractionFixture(
        fixture_id="art_001",
        content_type="Article",
        content="Some article text.",
        expected_topic_card={"extracted_title": "Some title"},
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        f.fixture_id = "art_002"  # type: ignore[misc]


def test_topic_card_fields_lists_canonical_field_names():
    fields = TopicCardFields.canonical()
    assert fields == (
        "extracted_title",
        "core_mechanism",
        "best_example",
        "main_tension",
        "transferable_pattern",
        "candidate_tie_backs",
    )


def test_extraction_diff_report_carries_per_field_compare():
    report = ExtractionDiffReport(
        variant_a="v5_baseline",
        variant_b="v6_candidate",
        per_field={"extracted_title": {"a": "T1", "b": "T1_alt"}},
        per_field_scores={"extracted_title": 0.0},
    )
    assert report.variant_a == "v5_baseline"
    assert report.per_field["extracted_title"]["b"] == "T1_alt"
