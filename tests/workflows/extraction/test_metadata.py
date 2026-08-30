"""workflows.extraction.metadata — one structured call over the fetched body.

The LLM call is mocked at the import location; what these tests pin is the
wiring contract, not label quality (which is judged from production output,
not from asserts): the message layout keeps the article cache-aligned with the
extraction lane's other structured calls, and every reply the schema does not
accept raises rather than being coerced into a half-populated payload.
"""

from unittest.mock import patch

import pytest
from workflows.extraction.metadata import MetadataPayload, extract_metadata
from workflows.extraction.shared_prefix import SHARED_SYSTEM
from workflows.llm import LLMCall

_PROMPT = "METADATA_PROMPT_BODY"


def _call(content: str, *, finish_reason: str = "stop") -> LLMCall:
    return LLMCall(
        content=content,
        model="gpt-5-mini",
        input_tokens=900,
        output_tokens=120,
        cached_tokens=800,
        finish_reason=finish_reason,
    )


def _reply(**overrides) -> str:
    payload = {
        "contributors": [{"name": "Kyle Cheung", "role": "author", "affiliation": "Greybeam"}],
        "publisher": "Orchestra",
    }
    payload.update(overrides)
    import json

    return json.dumps(payload)


def test_parses_reply_and_sends_the_article_ahead_of_the_task():
    """The article must sit in its own message ahead of the task tail, behind the
    same shared system message the topic_card / followups calls send — that
    ordering is what lets OpenAI serve this body from the prefix cache the
    extraction lane shares."""
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        return_value=_call(_reply()),
    ) as llm:
        payload, call = extract_metadata(
            "the article body",
            content_type="article",
            prompt=_PROMPT,
            model="gpt-5-mini",
        )

    assert isinstance(payload, MetadataPayload)
    assert [c.name for c in payload.contributors] == ["Kyle Cheung"]
    assert payload.contributors[0].affiliation == "Greybeam"
    assert payload.publisher == "Orchestra"
    assert call.cached_tokens == 800

    messages = llm.call_args.args[0]
    assert messages[0] == {"role": "system", "content": SHARED_SYSTEM}
    assert messages[1]["content"].endswith("the article body")
    assert _PROMPT in messages[2]["content"]


def test_re_asks_once_with_the_validation_error_when_the_reply_misses_the_schema():
    """JSON mode guarantees valid JSON, not a reply that satisfies the model — a
    stochastic wrong field name is the common failure, and naming it back to the
    model fixes it. Dropping the observation instead is the one cost this asset
    exists to avoid, and the SDK's own retries cannot help: the HTTP call
    succeeded, the validation did not."""
    good = _reply()
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        side_effect=[_call('{"contributor": []}'), _call(good)],
    ) as llm:
        payload, call = extract_metadata(
            "body",
            content_type="article",
            prompt=_PROMPT,
            model="gpt-5-mini",
        )

    assert payload.publisher == "Orchestra"
    assert llm.call_count == 2
    # The correction rides in the task tail, never ahead of the article — a
    # correction written into the prefix would cost the body's cache on the retry.
    retry_task = llm.call_args.args[0][2]["content"]
    assert "contributor" in retry_task
    # Cost across both attempts is visible, not just the winning one.
    assert call.input_tokens == 1800


def test_gives_up_after_repeated_schema_failures_rather_than_looping():
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        return_value=_call('{"contributor": []}'),
    ) as llm:
        with pytest.raises(ValueError):
            extract_metadata(
                "body",
                content_type="article",
                prompt=_PROMPT,
                model="gpt-5-mini",
            )
    assert llm.call_count == 3


def test_a_reply_omitting_an_empty_list_still_validates():
    """Content naming nobody is a correct answer, and a model that omits
    `contributors` rather than sending `[]` is making a cosmetic choice.
    Discarding the reply over it would lose the publisher, which does have a
    verifiable right answer."""
    import json as _json

    minimal = _json.dumps({"publisher": "Orchestra"})
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        return_value=_call(minimal),
    ):
        payload, _ = extract_metadata(
            "body",
            content_type="article",
            prompt=_PROMPT,
            model="gpt-5-mini",
        )
    assert payload.contributors == []
    assert payload.publisher == "Orchestra"


def test_treats_a_truncated_reply_as_invalid_output():
    """A reply cut off at the token ceiling can still be valid JSON — a
    contributor list that stops halfway parses clean and reads as complete. The
    stop reason is the only evidence it was cut, so it is checked before the
    payload is trusted."""
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        return_value=_call(_reply(), finish_reason="length"),
    ):
        with pytest.raises(ValueError, match="truncated"):
            extract_metadata(
                "body",
                content_type="article",
                prompt=_PROMPT,
                model="gpt-5-mini",
            )
