"""Tests for ExtractionCallRecord — the in-memory dataclass carrying one LLM
call's output + provenance from the extractor to the queue_store writer."""

from domains.extraction.records import ExtractionCallRecord


def test_extraction_call_record_minimal_construction():
    record = ExtractionCallRecord(
        call_kind="narrative",
        prompt_label="narrative_v1",
        prompt_sha256="a" * 64,
        schema_name=None,
        output="# Hi",
        tokens_in=100,
        tokens_out=50,
        cached_tokens=None,
        duration_ms=1234.5,
        extracted_at="2026-06-03T12:00:00+00:00",
    )
    assert record.call_kind == "narrative"
    assert record.schema_name is None
    assert record.node_metadata is None


def test_extraction_call_record_carries_optional_node_metadata():
    record = ExtractionCallRecord(
        call_kind="topic_card",
        prompt_label="topic_card_v1",
        prompt_sha256="b" * 64,
        schema_name="TopicCard",
        output='{"extracted_title": "x"}',
        tokens_in=100,
        tokens_out=50,
        cached_tokens=80,
        duration_ms=1234.5,
        extracted_at="2026-06-03T12:00:00+00:00",
        node_metadata={"node_id": "n1", "revision_count": 0},
    )
    assert record.node_metadata == {"node_id": "n1", "revision_count": 0}
    assert record.cached_tokens == 80
