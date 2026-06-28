"""Cross-repo schema-drift detection against a pinned JSON fixture.

The fixture `fixtures/extraction_payload_v1.json` is mirrored byte-for-byte
in newsletter-assistant's equivalent test directory. Any field rename or
removal on either side surfaces here as a `ValidationError`. Any added field
on this side that isn't reflected in the fixture surfaces as a non-matching
round-trip. NA's mirror test catches the inverse.

Fixture-versioning convention: when schemas evolve, add `extraction_payload_v2.json`
alongside v1 and write a v2 test. Never delete the older fixture — old fixtures
serve as backward-compat regression tests for the read path.
"""

import json
from pathlib import Path

from domains.extraction.schemas import ExtractionPayload

FIXTURES = Path(__file__).parent / "fixtures"


def test_extraction_payload_v1_fixture_parses_against_current_schema():
    fixture_path = FIXTURES / "extraction_payload_v1.json"
    raw = fixture_path.read_text()
    payload = ExtractionPayload.model_validate_json(raw)
    # Round-trip equality (object → JSON → object) confirms no field is silently dropped.
    rehydrated = ExtractionPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload


def test_extraction_payload_v2_fixture_parses_against_current_schema():
    fixture_path = FIXTURES / "extraction_payload_v2.json"
    raw = fixture_path.read_text()
    payload = ExtractionPayload.model_validate_json(raw)
    # Round-trip equality (object → JSON → object) confirms no field is silently dropped.
    rehydrated = ExtractionPayload.model_validate_json(payload.model_dump_json())
    assert rehydrated == payload


def test_extraction_payload_v2_fixture_field_names_match_current_schema():
    """A field rename on one side leaves stale keys in the fixture or new keys
    in the schema; this test flags either by comparing top-level + nested keys.
    Pinned to the latest fixture version (v2 added `followups.reader_threads`);
    v1 stays frozen as the backward-compat read-path test above."""
    raw = json.loads((FIXTURES / "extraction_payload_v2.json").read_text())
    payload = ExtractionPayload.model_validate_json(json.dumps(raw))
    assert set(raw) == set(payload.model_dump().keys())
    assert set(raw["topic_card"]) == set(payload.topic_card.model_dump().keys())
    assert set(raw["followups"]) == set(payload.followups.model_dump().keys())
