"""source_writer.summarize_source — turn one article into a SourceSummary.

The LLM call is mocked at the module boundary (claim-extraction quality is
validated empirically, not here); these tests pin the wiring contract: the
source's item_id and content_date are stamped onto the summary, the LLM's
tagged bullets are parsed into claims, and a `NONE` response yields an empty
summary rather than an error.
"""

from datetime import date
from unittest.mock import patch

from domains.types import IngestItem
from domains.wiki.source_summary import SourceClaim
from workflows.llm import LLMCall
from workflows.wiki_synthesis.source_writer import summarize_source


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
        "- [fact] Claude Code shipped subagents in March 2026.\n"
        "- [speculation] Agentic orchestration will replace most RAG by 2027.\n"
    )

    with patch(
        "workflows.wiki_synthesis.source_writer.generate_with_usage",
        return_value=_call(llm_output),
    ):
        summary, _call_meta = summarize_source(_item())

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


def test_none_response_yields_an_empty_summary_not_an_error():
    with patch(
        "workflows.wiki_synthesis.source_writer.generate_with_usage",
        return_value=_call("NONE"),
    ):
        summary, _call_meta = summarize_source(_item())

    assert summary.item_id == "medium::https://x.com/a"
    assert summary.claims == []
