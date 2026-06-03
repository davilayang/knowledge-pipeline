"""Tests for ThreeCallOpenAIExtractor — the v2 extractor strategy.

Mocks the AsyncOpenAI client surface (`chat.completions.create` and
`beta.chat.completions.parse`). Async surface is exercised end-to-end via
the sync `.extract()` entry point (which wraps `asyncio.run`).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard
from orchestrators.defs.extract_complex_contents.extractors.three_call_openai import (
    ThreeCallOpenAIExtractor,
)


def _topic_card_obj() -> TopicCard:
    return TopicCard(
        extracted_title="t",
        core_mechanism="m",
        best_example="e",
        transferable_pattern="p",
        main_tension="x",
    )


def _followups_obj() -> Followups:
    return Followups(questions=["a?", "b?", "c?", "d?"])


def _usage(prompt_tokens=100, completion_tokens=50, cached=80):
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.prompt_tokens_details = MagicMock()
    usage.prompt_tokens_details.cached_tokens = cached
    return usage


def _create_resp(text: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = text
    resp.usage = _usage()
    return resp


def _parse_resp(parsed_obj):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.parsed = parsed_obj
    resp.usage = _usage()
    return resp


def _wire_client(create_text: str, topic_obj, followups_obj):
    """Returns a mock AsyncOpenAI client wired with the right async surface."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_create_resp(create_text))

    async def _parse(*, model, max_tokens, messages, response_format):
        if response_format is TopicCard:
            return _parse_resp(topic_obj)
        if response_format is Followups:
            return _parse_resp(followups_obj)
        raise AssertionError(f"unexpected response_format: {response_format}")

    client.beta.chat.completions.parse = AsyncMock(side_effect=_parse)
    return client


@pytest.fixture
def extractor() -> ThreeCallOpenAIExtractor:
    return ThreeCallOpenAIExtractor(
        api_key="test",
        model="gpt-4.1-mini",
        narrative_prompt="NARRATIVE_PROMPT",
        narrative_prompt_label="narrative_v1",
        topic_card_prompt="TOPIC_CARD_PROMPT",
        topic_card_prompt_label="topic_card_v1",
        followups_prompt="FOLLOWUPS_PROMPT",
        followups_prompt_label="followups_v1",
    )


def test_extract_returns_extraction_payload_with_three_call_outputs(extractor):
    client = _wire_client("# narrative body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        payload, calls = extractor.extract(content="raw", content_type="Article")
    assert isinstance(payload, ExtractionPayload)
    assert payload.narrative_md == "# narrative body"
    assert payload.topic_card.extracted_title == "t"
    assert len(payload.followups.questions) == 4


def test_extract_returns_one_call_record_per_call(extractor):
    client = _wire_client("# narrative body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article")
    assert len(calls) == 3
    kinds = {c.call_kind for c in calls}
    assert kinds == {"narrative", "topic_card", "followups"}
    for c in calls:
        assert isinstance(c, ExtractionCallRecord)


def test_extract_records_carry_prompt_label_and_sha(extractor):
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article")
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].prompt_label == "narrative_v1"
    assert by_kind["topic_card"].prompt_label == "topic_card_v1"
    assert by_kind["followups"].prompt_label == "followups_v1"
    for kind, c in by_kind.items():
        assert len(c.prompt_sha256) == 64
        assert c.tokens_in == 100
        assert c.tokens_out == 50


def test_narrative_record_has_schema_name_none(extractor):
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article")
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].schema_name is None
    assert by_kind["topic_card"].schema_name == "TopicCard"
    assert by_kind["followups"].schema_name == "Followups"


def test_structured_calls_store_pydantic_json_output(extractor):
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article")
    by_kind = {c.call_kind: c for c in calls}
    # topic_card and followups outputs are pydantic JSON, parseable back
    TopicCard.model_validate_json(by_kind["topic_card"].output)
    Followups.model_validate_json(by_kind["followups"].output)


def test_extract_passes_content_type_tag_in_user_message(extractor):
    """Each call receives `[content_type: …]` in the user message so the prompt's
    content-type routing block can branch."""
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw content", content_type="YouTube")
    create_call = client.chat.completions.create.await_args
    user_msg = create_call.kwargs["messages"][1]["content"]
    assert "[content_type: YouTube]" in user_msg
    assert "raw content" in user_msg


def test_extract_raises_when_topic_card_call_fails(extractor):
    """asyncio.gather(return_exceptions=True) catches the exception; wrapper
    re-raises so Dagster retries the asset (we don't ship partial extractions)."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_create_resp("narrative"))

    async def _parse(*, model, max_tokens, messages, response_format):
        if response_format is TopicCard:
            raise RuntimeError("topic_card OpenAI 500")
        return _parse_resp(_followups_obj())

    client.beta.chat.completions.parse = AsyncMock(side_effect=_parse)

    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="topic_card OpenAI 500"):
            extractor.extract(content="raw", content_type="Article")


def test_bundle_sha256_changes_when_any_prompt_changes():
    base = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        narrative_prompt="N1",
        narrative_prompt_label="narrative_v1",
        topic_card_prompt="T1",
        topic_card_prompt_label="topic_card_v1",
        followups_prompt="F1",
        followups_prompt_label="followups_v1",
    )
    diff_narrative = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        narrative_prompt="N2",
        narrative_prompt_label="narrative_v1",
        topic_card_prompt="T1",
        topic_card_prompt_label="topic_card_v1",
        followups_prompt="F1",
        followups_prompt_label="followups_v1",
    )
    assert base.bundle_sha256 != diff_narrative.bundle_sha256
    assert base.bundle_label == "3call_v1"
