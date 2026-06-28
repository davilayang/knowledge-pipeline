"""Tests for ThreeCallOpenAIExtractor — the v2 extractor strategy.

Mocks the AsyncOpenAI client surface (`chat.completions.create` and
`beta.chat.completions.parse`). Async surface is exercised end-to-end via
the sync `.extract()` entry point (which wraps `asyncio.run`).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.schemas import ExtractionPayload, Followups, TopicCard
from workflows.extraction import PromptBundle
from workflows.extraction.three_call_openai import (
    ThreeCallOpenAIExtractor,
    _sha,
)


def _bundle(
    *,
    narrative_text: str = "NARRATIVE_PROMPT",
    narrative_label: str = "narrative_v1",
    topic_card_text: str = "TOPIC_CARD_PROMPT",
    topic_card_label: str = "topic_card_v1",
    followups_text: str = "FOLLOWUPS_PROMPT",
    followups_label: str = "followups_v1",
) -> PromptBundle:
    return PromptBundle(
        narrative=(narrative_text, narrative_label),
        topic_card=(topic_card_text, topic_card_label),
        followups=(followups_text, followups_label),
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
    """Returns a mock AsyncOpenAI client wired with the right async surface.
    `close()` is an AsyncMock — the extractor awaits it in `finally` for
    httpx-pool cleanup (introduced in the PR #79 review-fix pass)."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_create_resp(create_text))
    client.close = AsyncMock()

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
        prompt_sets={"unknown": _bundle()},
    )


def test_extract_returns_extraction_payload_with_three_call_outputs(extractor):
    client = _wire_client("# narrative body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        payload, calls = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert isinstance(payload, ExtractionPayload)
    assert payload.narrative_md == "# narrative body"
    assert payload.topic_card.extracted_title == "t"
    assert len(payload.followups.questions) == 4


def test_extract_returns_one_call_record_per_call(extractor):
    client = _wire_client("# narrative body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert len(calls) == 3
    kinds = {c.call_kind for c in calls}
    assert kinds == {"narrative", "topic_card", "followups"}
    for c in calls:
        assert isinstance(c, ExtractionCallRecord)


def test_extract_records_carry_prompt_label_and_sha(extractor):
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].prompt_label == "narrative_v1"
    assert by_kind["topic_card"].prompt_label == "topic_card_v1"
    assert by_kind["followups"].prompt_label == "followups_v1"
    for c in by_kind.values():
        assert len(c.prompt_sha256) == 64
        assert c.tokens_in == 100
        assert c.tokens_out == 50


def test_narrative_record_has_schema_name_none(extractor):
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].schema_name is None
    assert by_kind["topic_card"].schema_name == "TopicCard"
    assert by_kind["followups"].schema_name == "Followups"


def test_structured_calls_store_pydantic_json_output(extractor):
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    by_kind = {c.call_kind: c for c in calls}
    TopicCard.model_validate_json(by_kind["topic_card"].output)
    Followups.model_validate_json(by_kind["followups"].output)


def test_extract_passes_content_type_tag_in_user_message(extractor):
    """Each call receives `[content_type: …]` in the user message so the prompt's
    content-type routing block can branch."""
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw content", content_type="YouTube", content_shape="unknown")
    create_call = client.chat.completions.create.await_args
    user_msg = create_call.kwargs["messages"][1]["content"]
    assert "[content_type: YouTube]" in user_msg
    assert "raw content" in user_msg


def test_extract_raises_when_topic_card_call_fails(extractor):
    """asyncio.gather(return_exceptions=True) catches the exception; wrapper
    re-raises so Dagster retries the asset (we don't ship partial extractions)."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_create_resp("narrative"))
    client.close = AsyncMock()

    async def _parse(*, model, max_tokens, messages, response_format):
        if response_format is TopicCard:
            raise RuntimeError("topic_card OpenAI 500")
        return _parse_resp(_followups_obj())

    client.beta.chat.completions.parse = AsyncMock(side_effect=_parse)

    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="topic_card OpenAI 500"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")


def test_extract_closes_async_client_on_success(extractor):
    """Client must be closed in the same event loop that opened it — the
    asyncio.run loop dies on return, taking any unclosed httpx pool with it."""
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    client.close.assert_awaited_once()


def test_extract_closes_async_client_even_on_failure(extractor):
    """Client close must fire in `finally`, not only on the success path."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("narrative 500"))
    client.beta.chat.completions.parse = AsyncMock()
    client.close = AsyncMock()
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="narrative 500"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    client.close.assert_awaited_once()


# -------- bundle_label + bundle_sha256 --------


def test_bundle_label_is_shape_routed_v2():
    """Bumped from `3call_v1` because routing semantics changed — re-extract
    sensor cohort comparison flags existing rows as stale on the next tick."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle()},
    )
    assert ex.bundle_label == "3call_v2_shape_routed"


def test_bundle_sha256_changes_when_model_changes():
    """Model bump must flip cohort-staleness signal — without this an
    OPENAI_MODEL upgrade would leave existing rows looking fresh."""
    base = ThreeCallOpenAIExtractor(
        api_key="t", model="gpt-4o-mini", prompt_sets={"unknown": _bundle()}
    )
    upgraded = ThreeCallOpenAIExtractor(
        api_key="t", model="gpt-4o", prompt_sets={"unknown": _bundle()}
    )
    assert base.bundle_sha256("unknown") != upgraded.bundle_sha256("unknown")


def test_bundle_sha256_changes_when_any_prompt_changes():
    base = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle(narrative_text="N1")},
    )
    diff_narrative = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle(narrative_text="N2")},
    )
    assert base.bundle_sha256("unknown") != diff_narrative.bundle_sha256("unknown")


def test_bundle_sha_only_reflects_selected_bundle():
    """Critical isolation property: adding a new shape's bundle MUST NOT
    change `bundle_sha256(other_shape)`. Otherwise registering one new
    per-shape prompt set invalidates every prior row across all shapes."""
    single_shape = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle(narrative_text="UN")},
    )
    multi_shape = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={
            "unknown": _bundle(narrative_text="UN"),
            "conference_talk": _bundle(narrative_text="CT", narrative_label="narrative_ct_v1"),
        },
    )
    assert single_shape.bundle_sha256("unknown") == multi_shape.bundle_sha256("unknown")
    assert multi_shape.bundle_sha256("conference_talk") != multi_shape.bundle_sha256("unknown")


def test_constructor_raises_when_unknown_bundle_missing():
    """The `unknown` bundle is the generic fallback for any content_shape
    that has no shape-specific entry. Missing it would crash `extract` on
    the first row from a new shape — fail fast at construction instead."""
    with pytest.raises(ValueError, match="unknown"):
        ThreeCallOpenAIExtractor(
            api_key="t",
            model="gpt-4.1-mini",
            prompt_sets={"conference_talk": _bundle()},
        )


def test_extract_selects_shape_specific_bundle_when_present():
    """When the caller passes a content_shape that has a registered bundle,
    that bundle's prompts drive the calls — not the unknown fallback."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={
            "unknown": _bundle(
                narrative_text="GENERIC_NARRATIVE", narrative_label="narrative_generic"
            ),
            "conference_talk": _bundle(
                narrative_text="CT_NARRATIVE", narrative_label="narrative_ct_v1"
            ),
        },
    )
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(ex, "_client", client):
        _, calls = ex.extract(
            content="raw", content_type="YouTube", content_shape="conference_talk"
        )
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].prompt_label == "narrative_ct_v1"
    sys_msg = client.chat.completions.create.await_args.kwargs["messages"][0]["content"]
    assert sys_msg == "CT_NARRATIVE"


def test_extract_falls_back_to_unknown_for_unregistered_shape():
    """A content_shape with no registered bundle (yet) routes to the
    `unknown` fallback. Future shapes can be wired without crashing
    in-flight rows captured under the old extractor build."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={
            "unknown": _bundle(
                narrative_text="GENERIC_NARRATIVE", narrative_label="narrative_generic"
            ),
        },
    )
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(ex, "_client", client):
        _, calls = ex.extract(content="raw", content_type="YouTube", content_shape="tutorial")
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].prompt_label == "narrative_generic"


def test_extract_records_carry_prompt_set_shape_for_registered_shape():
    """Each ExtractionCallRecord records which shape's bundle fired —
    so downstream eval queries can group by `prompt_set_shape` independent
    of the queue_items.content_shape value at query time."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={
            "unknown": _bundle(),
            "conference_talk": _bundle(narrative_label="narrative_ct_v1"),
        },
    )
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(ex, "_client", client):
        _, calls = ex.extract(
            content="raw", content_type="YouTube", content_shape="conference_talk"
        )
    for c in calls:
        assert c.prompt_set_shape == "conference_talk"


def test_extract_records_prompt_set_shape_as_unknown_on_fallback():
    """Unregistered shape resolves to the `unknown` bundle — the record's
    `prompt_set_shape` reflects the bundle that ACTUALLY ran, not what the
    caller requested. Lets eval queries reason about provenance honestly."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle()},
    )
    client = _wire_client("body", _topic_card_obj(), _followups_obj())
    with patch.object(ex, "_client", client):
        _, calls = ex.extract(content="raw", content_type="YouTube", content_shape="tutorial")
    for c in calls:
        assert c.prompt_set_shape == "unknown"


def test_bundle_sha256_falls_back_to_unknown_for_unregistered_shape():
    """Symmetric to extract's fallback — sha for an unregistered shape
    equals sha for the unknown bundle. Lets staleness comparisons stay
    deterministic across deploys that register new shapes."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle()},
    )
    assert ex.bundle_sha256("conference_talk") == ex.bundle_sha256("unknown")


# -------- user_notes / reader_threads --------


def _wire_client_capturing(captured, create_text, topic_obj, followups_obj):
    """Like _wire_client but records the `messages` passed to each call,
    keyed by call kind, so tests can assert on the constructed prompts."""
    client = MagicMock()

    async def _create(*, model, max_tokens, messages):
        captured["narrative"] = messages
        return _create_resp(create_text)

    async def _parse(*, model, max_tokens, messages, response_format):
        if response_format is TopicCard:
            captured["topic_card"] = messages
            return _parse_resp(topic_obj)
        if response_format is Followups:
            captured["followups"] = messages
            return _parse_resp(followups_obj)
        raise AssertionError(f"unexpected response_format: {response_format}")

    client.chat.completions.create = AsyncMock(side_effect=_create)
    client.beta.chat.completions.parse = AsyncMock(side_effect=_parse)
    client.close = AsyncMock()
    return client


def _followups_sha(extractor, *, user_notes):
    captured = {}
    client = _wire_client_capturing(captured, "# n", _topic_card_obj(), _followups_obj())
    with patch.object(extractor, "_client", client):
        _payload, calls = extractor.extract(
            content="raw",
            content_type="Article",
            content_shape="unknown",
            user_notes=user_notes,
        )
    by_kind = {c.call_kind: c for c in calls}
    return captured, by_kind


def test_no_user_notes_leaves_followups_unchanged(extractor):
    captured, _ = _followups_sha(extractor, user_notes=None)
    assert "reader's notes" not in captured["followups"][1]["content"]
    assert "reader_threads" not in captured["followups"][0]["content"]


def test_user_notes_injected_only_into_followups(extractor):
    captured, _ = _followups_sha(extractor, user_notes="- compare with dbt")
    # followups user message carries the labeled notes block verbatim
    fu_user = captured["followups"][1]["content"]
    assert "[reader's notes — NOT part of the source article]" in fu_user
    assert "compare with dbt" in fu_user
    # followups system prompt carries the fold instruction
    assert "reader_threads" in captured["followups"][0]["content"]
    # topic_card + narrative are untouched
    assert "reader's notes" not in captured["topic_card"][1]["content"]
    assert "reader's notes" not in captured["narrative"][1]["content"]


def test_followups_sha_reflects_notes_topic_card_does_not(extractor):
    _, base = _followups_sha(extractor, user_notes=None)
    _, noted = _followups_sha(extractor, user_notes="- compare with dbt")
    assert noted["followups"].prompt_sha256 != base["followups"].prompt_sha256
    assert noted["topic_card"].prompt_sha256 == base["topic_card"].prompt_sha256
    # Positive assertion: the no-notes followups sha equals the sha of the raw
    # base followups prompt text, proving it's carried through unmodified and
    # not recomputed from a mutated value.
    assert base["followups"].prompt_sha256 == _sha(_bundle().followups[0])
