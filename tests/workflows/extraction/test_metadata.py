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
        "delivery_shape": None,
        "parts": [],
        "unreadable": [],
    }
    payload.update(overrides)
    import json

    return json.dumps(payload)


def test_parses_reply_and_sends_the_article_ahead_of_the_task():
    """The article must sit in its own message ahead of the task tail, behind the
    same shared system message the topic_card / followups calls send — that
    ordering is what lets OpenAI serve this body from the prefix cache the
    extraction lane shares. Per-item evidence rides in the tail, never the prefix."""
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        return_value=_call(_reply()),
    ) as llm:
        payload, call = extract_metadata(
            "the article body",
            content_type="article",
            evidence="title: The Rise of Multi-Query Engines",
            prompt=_PROMPT,
            model="gpt-5-mini",
        )

    assert isinstance(payload, MetadataPayload)
    assert [c.name for c in payload.contributors] == ["Kyle Cheung"]
    assert payload.contributors[0].affiliation == "Greybeam"
    assert payload.publisher == "Orchestra"
    assert payload.delivery_shape is None
    assert call.cached_tokens == 800

    messages = llm.call_args.args[0]
    assert messages[0] == {"role": "system", "content": SHARED_SYSTEM}
    assert messages[1]["content"].endswith("the article body")
    assert _PROMPT in messages[2]["content"]
    assert "The Rise of Multi-Query Engines" in messages[2]["content"]


def test_rejects_a_delivery_shape_outside_the_settled_set():
    """`delivery_shape` is two values plus null. A third label is a reply the
    schema never declared, and Phase B's contract is to write nothing rather than
    store a value no consumer will know how to read."""
    with patch(
        "workflows.extraction.metadata.generate_messages_with_usage",
        return_value=_call(_reply(delivery_shape="builds_in_order")),
    ):
        with pytest.raises(ValueError):
            extract_metadata(
                "body",
                content_type="article",
                evidence="",
                prompt=_PROMPT,
                model="gpt-5-mini",
            )


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
                evidence="",
                prompt=_PROMPT,
                model="gpt-5-mini",
            )
