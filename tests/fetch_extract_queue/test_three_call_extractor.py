"""Tests for ThreeCallOpenAIExtractor — the v2 extractor strategy.

Mocks the AsyncOpenAI client surface. All three calls go through
`chat.completions.create`; the structured pair runs in JSON mode and is told
apart by the prompt text in its trailing task message. Async surface is
exercised end-to-end via the sync `.extract()` entry point (which wraps
`asyncio.run`).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from domains.extraction.records import ExtractionCallRecord
from domains.extraction.render import render_narrative
from domains.extraction.schemas import ExtractionPayload, Followups, Narrative, TopicCard
from workflows.extraction import PromptBundle
from workflows.extraction.shared_prefix import effective_prompt_sha
from workflows.extraction.three_call_openai import (
    EXTRACTION_CACHE_KEY,
    ThreeCallOpenAIExtractor,
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


def _narrative_obj() -> Narrative:
    return Narrative(
        speakers_and_author="Alice Nkemdirim (Acme)",
        structure="one throughline - argues the core idea",
        core_idea="The core idea.",
        load_bearing_claims=["Claim one - anchor", "Claim two - anchor"],
        delivery_beats=["Beat one\nAnchor: a figure"],
        named_concepts_and_entities="Alice Nkemdirim, Acme",
    )


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
    # Real responses carry refusal=None when the model did not refuse; a bare
    # MagicMock would auto-create a truthy attribute and trip the refusal guard.
    resp.choices[0].message.refusal = None
    resp.choices[0].finish_reason = "stop"
    resp.usage = _usage()
    return resp


def _wire_client(create_text=None, topic_obj=None, followups_obj=None, *, capture=None):
    """Mock client for all three calls. Accepts any kwargs, so a call may send
    either `max_tokens` or `max_completion_tokens` without the mock rejecting the
    keyword. `capture`, when given, collects each call's `messages` by kind."""
    create_text = _narrative_obj().model_dump_json() if create_text is None else create_text
    topic_obj = topic_obj if topic_obj is not None else _topic_card_obj()
    followups_obj = followups_obj if followups_obj is not None else _followups_obj()
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        messages = kwargs["messages"]
        joined = "".join(m["content"] for m in messages)
        if "NARRATIVE" in joined:
            if capture is not None:
                capture["narrative"] = messages
            return _create_resp(create_text)
        is_topic = "TOPIC_CARD_PROMPT" in messages[-1]["content"]
        if capture is not None:
            capture["topic_card" if is_topic else "followups"] = messages
        return _create_resp((topic_obj if is_topic else followups_obj).model_dump_json())

    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


def _narrative_kwargs(client) -> dict:
    """All three calls send json now, so the narrative is told apart by its
    prompt text in the trailing task message."""
    return next(
        c.kwargs
        for c in client.chat.completions.create.await_args_list
        if "NARRATIVE" in c.kwargs["messages"][-1]["content"]
    )


def _structured_kwargs(client) -> list[dict]:
    """The topic-card and follow-ups calls — the narrative sends json too now."""
    return [
        c.kwargs
        for c in client.chat.completions.create.await_args_list
        if "NARRATIVE" not in c.kwargs["messages"][-1]["content"]
    ]


@pytest.fixture
def extractor() -> ThreeCallOpenAIExtractor:
    return ThreeCallOpenAIExtractor(
        api_key="test",
        model="gpt-4.1-mini",
        prompt_sets={"unknown": _bundle()},
    )


def test_extract_returns_extraction_payload_with_three_call_outputs(extractor):
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        payload, calls = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert isinstance(payload, ExtractionPayload)
    assert payload.narrative_md == render_narrative(_narrative_obj())
    assert payload.topic_card.extracted_title == "t"
    assert len(payload.followups.questions) == 4


def test_extract_returns_one_call_record_per_call(extractor):
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert len(calls) == 3
    kinds = {c.call_kind for c in calls}
    assert kinds == {"narrative", "topic_card", "followups"}
    for c in calls:
        assert isinstance(c, ExtractionCallRecord)


def test_extract_records_carry_prompt_label_and_sha(extractor):
    client = _wire_client()
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


def test_every_call_record_names_the_schema_it_was_validated_against(extractor):
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].schema_name == "Narrative"
    assert by_kind["topic_card"].schema_name == "TopicCard"
    assert by_kind["followups"].schema_name == "Followups"


def test_structured_calls_store_pydantic_json_output(extractor):
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    by_kind = {c.call_kind: c for c in calls}
    TopicCard.model_validate_json(by_kind["topic_card"].output)
    Followups.model_validate_json(by_kind["followups"].output)


def test_extract_passes_content_type_tag_in_user_message(extractor):
    """Each call receives `[content_type: …]` in the user message so the prompt's
    content-type routing block can branch."""
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw content", content_type="YouTube", content_shape="unknown")
    create_call = client.chat.completions.create.await_args
    user_msg = create_call.kwargs["messages"][1]["content"]
    assert "[content_type: YouTube]" in user_msg
    assert "raw content" in user_msg


def test_gpt5_model_sends_reasoning_params_not_max_tokens():
    """gpt-5-family are reasoning models: they reject `max_tokens` and need
    `max_completion_tokens` + `reasoning_effort`. Extraction wants coverage,
    not deliberation, so effort is pinned to `minimal`."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t", model="gpt-5-mini", prompt_sets={"unknown": _bundle()}, max_tokens=4096
    )
    client = _wire_client()
    with patch.object(ex, "_client", client):
        ex.extract(content="raw", content_type="Article", content_shape="unknown")
    narr = _narrative_kwargs(client)
    assert narr["max_completion_tokens"] == 4096
    assert narr["reasoning_effort"] == "minimal"
    assert "max_tokens" not in narr
    for kwargs in _structured_kwargs(client):
        assert kwargs["max_completion_tokens"] == 4096
        assert kwargs["reasoning_effort"] == "minimal"
        assert "max_tokens" not in kwargs


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.6-luna"])
def test_dotted_gpt5_models_send_none_effort_not_minimal(model):
    """The don't-deliberate setting was renamed at the first dotted release:
    `gpt-5`/`gpt-5-mini` take `minimal` and reject `none`, while every dotted
    release from 5.4 on does the reverse. Both share the `gpt-5` prefix, so a
    branch that keys on the wrong boundary 400s a whole generation on every
    call — parametrised across releases because pinning one id is what let
    gpt-5.4 slip through."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t", model=model, prompt_sets={"unknown": _bundle()}, max_tokens=4096
    )
    client = _wire_client()
    with patch.object(ex, "_client", client):
        ex.extract(content="raw", content_type="Article", content_shape="unknown")
    narr = _narrative_kwargs(client)
    assert narr["max_completion_tokens"] == 4096
    assert narr["reasoning_effort"] == "none"
    assert "max_tokens" not in narr
    for kwargs in _structured_kwargs(client):
        assert kwargs["reasoning_effort"] == "none"


def test_non_reasoning_model_sends_max_tokens(extractor):
    """gpt-4.1-family keep the classic `max_tokens` param (they reject
    `reasoning_effort`)."""
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    narr = _narrative_kwargs(client)
    assert narr["max_tokens"] == 2048  # fixture uses the constructor default
    assert "max_completion_tokens" not in narr
    assert "reasoning_effort" not in narr


def test_extract_raises_when_topic_card_call_fails(extractor):
    """A failed structured call fails the whole extract — we don't ship partial
    extractions, and nothing downstream retries the asset, so the item is left
    for a manual re-queue rather than half-written."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        messages = kwargs["messages"]
        if "NARRATIVE" in messages[-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        if "TOPIC_CARD_PROMPT" in messages[-1]["content"]:
            raise RuntimeError("topic_card OpenAI 500")
        return _create_resp(_followups_obj().model_dump_json())

    client.chat.completions.create = AsyncMock(side_effect=_create)

    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="topic_card OpenAI 500"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")


def test_extract_closes_async_client_on_success(extractor):
    """Client must be closed in the same event loop that opened it — the
    asyncio.run loop dies on return, taking any unclosed httpx pool with it."""
    client = _wire_client()
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    client.close.assert_awaited_once()


def test_extract_closes_async_client_even_on_failure(extractor):
    """Client close must fire in `finally`, not only on the success path."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("narrative 500"))
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
    client = _wire_client()
    with patch.object(ex, "_client", client):
        _, calls = ex.extract(
            content="raw", content_type="YouTube", content_shape="conference_talk"
        )
    by_kind = {c.call_kind: c for c in calls}
    assert by_kind["narrative"].prompt_label == "narrative_ct_v1"
    assert _narrative_kwargs(client)["messages"][-1]["content"].startswith("CT_NARRATIVE")


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
    client = _wire_client()
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
    client = _wire_client()
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
    client = _wire_client()
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


def _followups_sha(extractor, *, user_notes):
    captured = {}
    client = _wire_client(capture=captured)
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
    # Keyed on the fold's exact label and wording: the generated schema block
    # carries the `reader_threads` field name and its description, which mentions
    # a bare "[reader's notes] block", whether or not the fold fired.
    assert (
        "[reader's notes — NOT part of the source article]"
        not in captured["followups"][-1]["content"]
    )
    assert "Never answer reader" not in captured["followups"][-1]["content"]


def test_user_notes_injected_only_into_followups(extractor):
    captured, _ = _followups_sha(extractor, user_notes="- compare with dbt")
    # The notes and the fold instruction both ride in the trailing task message.
    fu_task = captured["followups"][-1]["content"]
    assert "[reader's notes — NOT part of the source article]" in fu_task
    assert "compare with dbt" in fu_task
    assert "Never answer reader" in fu_task
    # topic_card + narrative are untouched
    assert "reader's notes" not in captured["topic_card"][-1]["content"]
    assert "reader's notes" not in captured["narrative"][-1]["content"]


def test_reader_notes_do_not_break_the_shared_prefix(extractor):
    """Reader notes are per-item data, so they must ride in the tail. Putting them
    in the article envelope would leave followups with a different prefix from
    topic_card on every annotated item, dropping the body out of the cache."""
    captured, _ = _followups_sha(extractor, user_notes="- compare with dbt")
    assert captured["followups"][:-1] == captured["topic_card"][:-1]


def test_followups_sha_reflects_notes_topic_card_does_not(extractor):
    _, base = _followups_sha(extractor, user_notes=None)
    _, noted = _followups_sha(extractor, user_notes="- compare with dbt")
    assert noted["followups"].prompt_sha256 != base["followups"].prompt_sha256
    assert noted["topic_card"].prompt_sha256 == base["topic_card"].prompt_sha256
    # Positive assertion: the no-notes followups sha is the sha of the effective
    # prompt built from the unmodified base text, proving it's carried through
    # and not recomputed from a mutated value.
    assert base["followups"].prompt_sha256 == effective_prompt_sha(
        _bundle().followups[0], Followups
    )


def test_every_call_declares_the_same_prompt_cache_key():
    """OpenAI steers requests sharing a `prompt_cache_key` to the same cache. These
    three carry different schemas so they cannot share the body's entry, but one
    shard per lane lets each call reach its own prior entry."""
    ex = ThreeCallOpenAIExtractor(
        api_key="t", model="gpt-4.1-mini", prompt_sets={"unknown": _bundle()}
    )
    client = _wire_client()
    with patch.object(ex, "_client", client):
        ex.extract(content="raw", content_type="Article", content_shape="unknown")

    keys = [
        c.kwargs.get("prompt_cache_key") for c in client.chat.completions.create.await_args_list
    ]

    assert len(keys) == 3
    assert all(k == EXTRACTION_CACHE_KEY for k in keys)


def _json_mode_client(topic_json: str, followups_json: str):
    """All three calls go through `create`; the structured pair runs in JSON mode
    and comes back as raw text the extractor has to parse and validate itself."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(*, messages, response_format=None, **_):
        if "NARRATIVE" in messages[-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        tail = messages[-1]["content"]
        return _create_resp(topic_json if "TOPIC_CARD_PROMPT" in tail else followups_json)

    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


def test_structured_call_parses_the_raw_json_body_into_the_schema(extractor):
    client = _json_mode_client(
        _topic_card_obj().model_dump_json(), _followups_obj().model_dump_json()
    )
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.topic_card.extracted_title == "t"


def test_the_structured_pair_does_not_overlap(extractor):
    """topic_card must finish before followups starts: the cached article body
    only reaches the second call once the first has written it. Running them
    concurrently leaves both paying full price for the body."""
    import asyncio

    events: list[str] = []

    async def _create(**kwargs):
        messages = kwargs["messages"]
        if "NARRATIVE" in messages[-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        kind = "topic_card" if "TOPIC_CARD_PROMPT" in messages[-1]["content"] else "followups"
        events.append(f"start {kind}")
        await asyncio.sleep(0)
        events.append(f"end {kind}")
        obj = _topic_card_obj() if kind == "topic_card" else _followups_obj()
        return _create_resp(obj.model_dump_json())

    client = MagicMock()
    client.close = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw", content_type="Article", content_shape="unknown")

    assert events == [
        "start topic_card",
        "end topic_card",
        "start followups",
        "end followups",
    ]


def _retrying_client(topic_bodies: list[str]):
    """Serves `topic_bodies` to successive topic_card attempts; followups and
    narrative always succeed."""
    client = MagicMock()
    client.close = AsyncMock()
    pending = list(topic_bodies)

    async def _create(**kwargs):
        messages = kwargs["messages"]
        if "NARRATIVE" in messages[-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        if "TOPIC_CARD_PROMPT" in messages[-1]["content"]:
            return _create_resp(pending.pop(0))
        return _create_resp(_followups_obj().model_dump_json())

    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


def test_structured_call_retries_when_the_json_fails_validation(extractor):
    """JSON mode guarantees valid json, not a valid TopicCard. A reply that
    parses but misses a required field is retried rather than failing the job."""
    client = _retrying_client(
        ['{"extracted_title": "only this"}', _topic_card_obj().model_dump_json()]
    )
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.topic_card.extracted_title == "t"


def test_structured_call_gives_up_after_three_attempts(extractor):
    """A model that has missed the schema three times fails the job rather than
    retrying forever or shipping a partial extraction."""
    client = _retrying_client(['{"extracted_title": "no"}'] * 3)
    with patch.object(extractor, "_client", client):
        # Not the bare pydantic error: the reason and the next step are what a
        # reader of the failed row gets, and pydantic names our data model.
        with pytest.raises(RuntimeError, match="no reply matched the schema"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert len(_structured_kwargs(client)) == 3


def test_the_retry_correction_leaves_the_cached_prefix_intact(extractor):
    """The validation error rides in the tail. Written ahead of the article body
    it would void the prompt cache on exactly the calls that retry."""
    client = _retrying_client(['{"extracted_title": "no"}', _topic_card_obj().model_dump_json()])
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    first, retry = (k["messages"] for k in _structured_kwargs(client)[:2])
    assert retry[:-1] == first[:-1]
    assert "rejected by the schema" in retry[-1]["content"]


def test_a_truncated_reply_fails_immediately_rather_than_retrying(extractor):
    """A reply cut off at the token ceiling will be cut off again on a retry with
    the same ceiling, so re-asking only burns calls and buries the real reason
    under a JSON parse error."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        if "NARRATIVE" in kwargs["messages"][-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        resp = _create_resp('{"extracted_title": "cut off here')
        resp.choices[0].finish_reason = "length"
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="completion ceiling"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert len(_structured_kwargs(client)) == 1


def test_an_undeclared_field_is_rejected_rather_than_silently_dropped(extractor):
    """Structured Outputs could not emit a key the schema never declared; JSON
    mode can, and pydantic's default `extra="ignore"` drops it without a word.
    On an OPTIONAL field that is silent data loss: a reply misspelling
    `reader_threads` still validates and the reader's own notes just vanish."""
    client = MagicMock()
    client.close = AsyncMock()
    replies = [
        '{"questions": ["a?", "b?", "c?", "d?"], "reader_notes": ["wanted X"]}',
        '{"questions": ["a?", "b?", "c?", "d?"], "reader_threads": ["wanted X"]}',
    ]

    async def _create(**kwargs):
        messages = kwargs["messages"]
        if "NARRATIVE" in messages[-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        if "TOPIC_CARD_PROMPT" in messages[-1]["content"]:
            return _create_resp(_topic_card_obj().model_dump_json())
        return _create_resp(replies.pop(0))

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.followups.reader_threads == ["wanted X"]


def test_bundle_sha_moves_when_the_shared_system_moves(monkeypatch):
    """`bundle_sha256` is the COHORT staleness signal written to
    `queue_items.extractor_sha256`. The shared system message and the generated
    schema are now part of what every structured call sends, so a hash over the
    prompt markdown alone would leave every existing row reading as fresh after
    an edit to either."""
    from workflows.extraction import shared_prefix

    ex = ThreeCallOpenAIExtractor(
        api_key="t", model="gpt-4.1-mini", prompt_sets={"unknown": _bundle()}
    )
    before = ex.bundle_sha256("unknown")
    monkeypatch.setattr(shared_prefix, "SHARED_SYSTEM", "A DIFFERENT SHARED SYSTEM")
    assert ex.bundle_sha256("unknown") != before


def test_a_refusal_fails_immediately_rather_than_retrying(extractor):
    """A refusal arrives with empty content, which would otherwise look like
    malformed JSON and burn three retries before failing with a parse error that
    says nothing about why the model declined."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        if "NARRATIVE" in kwargs["messages"][-1]["content"]:
            return _create_resp(_narrative_obj().model_dump_json())
        resp = _create_resp("")
        resp.choices[0].message.refusal = "I can't help with that."
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="refused"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert len(_structured_kwargs(client)) == 1


def test_a_non_object_json_reply_is_retried_not_crashed(extractor):
    """JSON mode guarantees valid json, which includes a bare `null` or number.
    Those have to go down the retry path like any other bad reply, not escape it
    as an unhandled TypeError from the undeclared-key check."""
    client = _retrying_client(["null", _topic_card_obj().model_dump_json()])
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.topic_card.extracted_title == "t"


def test_cached_tokens_stays_none_when_the_api_reports_no_cache_details(extractor):
    """`cached_tokens` is `int | None`, where None means the API did not report
    prefix-cache details at all — distinct from a reported zero. The narrative
    call preserves that; the structured pair must not flatten it to 0, or the
    two call kinds disagree about what an unreported value looks like."""
    client = _wire_client()

    async def _create(**kwargs):
        resp = _create_resp(
            _narrative_obj().model_dump_json()
            if "NARRATIVE" in kwargs["messages"][-1]["content"]
            else (
                _topic_card_obj().model_dump_json()
                if "TOPIC_CARD_PROMPT" in kwargs["messages"][-1]["content"]
                else _followups_obj().model_dump_json()
            )
        )
        resp.usage.prompt_tokens_details = None  # older/non-caching response shape
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        _, calls = extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert {c.call_kind: c.cached_tokens for c in calls} == {
        "narrative": None,
        "topic_card": None,
        "followups": None,
    }


def _narrative_client(narrative_bodies: list[str]):
    """Serves `narrative_bodies` to successive narrative attempts; the structured
    pair always succeeds."""
    client = MagicMock()
    client.close = AsyncMock()
    pending = list(narrative_bodies)

    async def _create(**kwargs):
        if "NARRATIVE" in kwargs["messages"][-1]["content"]:
            return _create_resp(pending.pop(0))
        obj = (
            _topic_card_obj()
            if "TOPIC_CARD_PROMPT" in kwargs["messages"][-1]["content"]
            else _followups_obj()
        )
        return _create_resp(obj.model_dump_json())

    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


def test_an_empty_narrative_is_retried(extractor):
    """The narrative call intermittently returns an empty completion. One retry
    recovers most of them; without it the whole item fails and the two
    structured results are discarded along with it."""
    client = _narrative_client(["", _narrative_obj().model_dump_json()])
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.narrative_md == render_narrative(_narrative_obj())


def test_an_exhausted_narrative_reports_what_happened_not_a_schema_error(extractor):
    """The message here is written for whoever reads the Notion row: a run-failure
    sensor copies the innermost exception message into the row's Error field. Left
    to pydantic it read `String should have at least 1 character` for
    `narrative_md`, which names our data model rather than the failure."""
    client = _narrative_client(["", "", ""])
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError) as exc:
            extractor.extract(content="raw", content_type="YouTube", content_shape="unknown")
    message = str(exc.value)
    assert "narrative" in message  # which of the three calls gave up
    assert "empty" in message  # the fault, not json.loads' "Expecting value"
    assert "3 attempts" in message
    assert "YouTube" in message  # the item, so the row is identifiable
    assert "Retry" in message  # what the reader should do about it


def test_a_refused_narrative_reports_the_refusal_not_an_empty_completion(extractor):
    """A refusal arrives as empty content, so without this it would be retried and
    then reported as 'OpenAI returned an empty narrative' — a confident, wrong
    diagnosis in the row the user reads."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        if "NARRATIVE" not in kwargs["messages"][-1]["content"]:
            return _create_resp(_topic_card_obj().model_dump_json())
        resp = _create_resp("")
        resp.choices[0].message.refusal = "I can't help with that."
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="refused"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    assert client.chat.completions.create.await_count == 1


def test_a_whitespace_only_narrative_counts_as_empty(extractor):
    """`"   "` clears both `if output` and pydantic's min_length=1, so without
    this it is stored as a narrative and the item silently carries a blank one."""
    client = _narrative_client(["   \n  ", _narrative_obj().model_dump_json()])
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.narrative_md == render_narrative(_narrative_obj())


def test_a_truncated_narrative_is_not_stored_as_a_whole_one(extractor):
    """`finish_reason="length"` means the model was cut off at the output ceiling.
    What came back still reads as a finished narrative, and the voice agent would
    speak it as one, so storing it is silent corruption rather than a short answer."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        if "NARRATIVE" not in kwargs["messages"][-1]["content"]:
            is_topic = "TOPIC_CARD_PROMPT" in kwargs["messages"][-1]["content"]
            obj = _topic_card_obj() if is_topic else _followups_obj()
            return _create_resp(obj.model_dump_json())
        resp = _create_resp("Salient threads:\n- one thing\n- a second thing, cut off mid-")
        resp.choices[0].finish_reason = "length"
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError):
            extractor.extract(content="raw", content_type="YouTube", content_shape="unknown")


def test_a_truncated_narrative_message_names_the_limit_and_the_next_step(extractor):
    """Covers the message text only — a run-failure sensor is what copies it into
    the Notion row's Error field, and that boundary is not exercised here. It is
    still the whole explanation the reader gets, and truncation and the empty-reply
    fault both surface as a failed item, so the message is what tells them apart."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        if "NARRATIVE" not in kwargs["messages"][-1]["content"]:
            is_topic = "TOPIC_CARD_PROMPT" in kwargs["messages"][-1]["content"]
            obj = _topic_card_obj() if is_topic else _followups_obj()
            return _create_resp(obj.model_dump_json())
        resp = _create_resp("Salient threads:\n- one thing, cut off mid-")
        resp.choices[0].finish_reason = "length"
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError) as exc:
            extractor.extract(content="raw", content_type="YouTube", content_shape="unknown")
    message = str(exc.value)
    assert "2048" in message  # the limit that was hit, not a bare "too long"
    assert "YouTube" in message  # the item, so the row is identifiable
    assert "Nothing was stored" in message  # what became of the half narrative
    assert "maintainer" in message  # who can act, since the reader cannot
    # The spent-token count is not narrative length on a reasoning model, and a
    # reader who takes it as one concludes the ceiling is far tighter than it is.
    assert "thinking" in message
    # "empty" belongs to the other narrative fault, which is transient and worth
    # retrying. Reusing the word here would point the reader at the wrong fix.
    assert "empty" not in message


def test_a_zero_byte_reply_at_the_limit_is_truncation_not_an_empty_narrative(extractor):
    """Reasoning models spend the completion budget on thinking as well as output,
    so hitting the limit can return nothing at all. That is not the transient empty
    reply the retry exists for — that one arrives with `finish_reason="stop"` and a
    second attempt clears it. Retrying this instead burns another full budget and
    then reports the wrong fault, so the length check has to come first."""
    client = MagicMock()
    client.close = AsyncMock()

    async def _create(**kwargs):
        if "NARRATIVE" not in kwargs["messages"][-1]["content"]:
            is_topic = "TOPIC_CARD_PROMPT" in kwargs["messages"][-1]["content"]
            obj = _topic_card_obj() if is_topic else _followups_obj()
            return _create_resp(obj.model_dump_json())
        resp = _create_resp("")
        resp.choices[0].finish_reason = "length"
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    with patch.object(extractor, "_client", client):
        with pytest.raises(RuntimeError, match="completion"):
            extractor.extract(content="raw", content_type="Article", content_shape="unknown")
    # One call: no retry, and the structured pair never ran.
    assert client.chat.completions.create.await_count == 1


def test_narrative_sends_the_same_cached_prefix_as_the_structured_calls(extractor):
    """Everything ahead of the task tail must be byte-identical across all three
    calls, or the narrative sits in a prompt-cache partition of its own and the
    article is billed twice per item instead of once."""
    capture: dict = {}
    client = _wire_client(
        _narrative_obj().model_dump_json(), _topic_card_obj(), _followups_obj(), capture=capture
    )
    with patch.object(extractor, "_client", client):
        extractor.extract(content="raw", content_type="Article", content_shape="unknown")

    assert capture["narrative"][:2] == capture["topic_card"][:2]


def test_a_blank_field_narrative_is_retried_not_stored(extractor):
    """`min_length=1` counts characters, so `"   "` clears it. The old narrative
    call stripped the whole completion before accepting it, which caught this;
    routing through the schema does not, and a narrative whose fields are blank
    renders as headed sections with nothing under them — which the voice agent
    reads as a source that had nothing to say."""
    blank = json.dumps(
        {
            "speakers_and_author": " ",
            "structure": "\n ",
            "core_idea": " ",
            "load_bearing_claims": ["   "],
            "delivery_beats": ["  "],
            "named_concepts_and_entities": "\n ",
        }
    )
    client = _narrative_client([blank, _narrative_obj().model_dump_json()])
    with patch.object(extractor, "_client", client):
        payload, _ = extractor.extract(
            content="raw", content_type="Article", content_shape="unknown"
        )
    assert payload.narrative_md == render_narrative(_narrative_obj())
