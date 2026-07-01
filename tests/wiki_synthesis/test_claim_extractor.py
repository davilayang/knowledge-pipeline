"""claim_extractor.extract_claims — turn one article into a ClaimSet.

The LLM call is mocked at the module boundary (claim-extraction quality is
validated empirically, not here); these tests pin the wiring contract: the
source's item_id and content_date are stamped onto the summary, the LLM's
tagged bullets are parsed into claims, a `NONE` response yields an empty
summary rather than an error, and the call is issued with the shared-prefix
message layout (so the article stays cache-aligned with extract_entities).
"""

from datetime import date
from unittest.mock import patch

from domains.types import IngestItem
from domains.wiki.claims import SourceClaim
from workflows.llm import LLMCall
from workflows.wiki_synthesis.claim_extractor import extract_claims


def _item(**overrides) -> IngestItem:
    fields = dict(
        item_id="medium::https://x.com/a",
        title="A piece on Claude Code",
        date=date(2026, 3, 15),
        text="body",
        source_type="raw_store",
        source_ref="raw_store:content_1",
        author=None,
    )
    fields.update(overrides)
    return IngestItem(**fields)


def _call(content: str) -> LLMCall:
    return LLMCall(content=content, model="gpt-4.1-mini", input_tokens=1, output_tokens=1)


def test_stamps_item_id_and_content_date_and_parses_tagged_claims():
    llm_output = (
        "- [reported] Claude Code shipped subagents in March 2026.\n"
        "- [opinion] Agentic orchestration will replace most RAG by 2027.\n"
    )

    with patch(
        "workflows.wiki_synthesis.claim_extractor.generate_messages_with_usage",
        return_value=_call(llm_output),
    ):
        summary, _call_meta = extract_claims(_item())

    assert summary.item_id == "medium::https://x.com/a"
    assert summary.content_date == "2026-03-15"
    assert summary.claims == [
        SourceClaim(
            text="Claude Code shipped subagents in March 2026.",
            source_id="medium::https://x.com/a",
            speculative=False,
        ),
        SourceClaim(
            text="Agentic orchestration will replace most RAG by 2027.",
            source_id="medium::https://x.com/a",
            speculative=True,
        ),
    ]


def test_uses_shared_prefix_layout():
    # The claims call must be [system, article envelope, task]: the first two
    # messages are the cacheable prefix shared with extract_entities; the task is
    # the differing tail. The article body lives in the envelope, not the task.
    captured = {}

    def fake(messages, *, model, temperature):
        captured["messages"] = messages
        return _call("- [reported] X shipped.")

    with patch(
        "workflows.wiki_synthesis.claim_extractor.generate_messages_with_usage", side_effect=fake
    ):
        extract_claims(_item(text="the article body about Claude Code"))

    messages = captured["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "user"]
    assert "the article body about Claude Code" in messages[1]["content"]
    assert "the article body about Claude Code" not in messages[2]["content"]


def test_none_response_yields_an_empty_summary_not_an_error(caplog):
    with patch(
        "workflows.wiki_synthesis.claim_extractor.generate_messages_with_usage",
        return_value=_call("NONE"),
    ):
        summary, _call_meta = extract_claims(_item())

    assert summary.item_id == "medium::https://x.com/a"
    assert summary.claims == []
    # NONE is an expected outcome — no warning.
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_spoken_content_shape_primes_the_task():
    captured = {}

    def fake(messages, *, model, temperature):
        captured["messages"] = messages
        return _call("- [opinion] The speaker predicts X.")

    with patch(
        "workflows.wiki_synthesis.claim_extractor.generate_messages_with_usage", side_effect=fake
    ):
        extract_claims(_item(), content_shape="podcast_episode")

    # The spoken prime rides in the task tail (last message), not the shared prefix.
    # Assert a marker UNIQUE to the prime (the general claim rules already mention
    # "prediction" for every shape, so that word can't distinguish primed calls).
    assert "most of what the speaker says" in captured["messages"][-1]["content"].lower()


def test_text_content_shape_does_not_prime():
    captured = {}

    def fake(messages, *, model, temperature):
        captured["messages"] = messages
        return _call("- [reported] X shipped.")

    with patch(
        "workflows.wiki_synthesis.claim_extractor.generate_messages_with_usage", side_effect=fake
    ):
        extract_claims(_item(), content_shape="opinion_essay")

    assert "most of what the speaker says" not in captured["messages"][-1]["content"].lower()


def test_malformed_response_with_no_tags_logs_a_warning(caplog):
    # The model ignored the format and answered in prose: zero claims parsed, but
    # this is a silent extraction failure (not an honest NONE) — surface it.
    with patch(
        "workflows.wiki_synthesis.claim_extractor.generate_messages_with_usage",
        return_value=_call("Here are the key claims: Claude Code shipped subagents."),
    ):
        summary, _call_meta = extract_claims(_item())

    assert summary.claims == []
    assert any(
        r.levelname == "WARNING" and "medium::https://x.com/a" in r.getMessage()
        for r in caplog.records
    )
