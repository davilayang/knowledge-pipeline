"""extract_shared.article_envelope — the article block both extract-time calls send.

The envelope numbers the body into citable units so a claim can cite the unit
it came from, and stays byte-identical between the claims and entities calls so
the article prompt-caches across the two.
"""

from datetime import date
from unittest.mock import patch

from domains.types import IngestItem
from domains.wiki.claims import ClaimSet
from workflows.llm import LLMCall
from workflows.wiki_synthesis.extract_claims import extract_claims
from workflows.wiki_synthesis.extract_entities import extract_entities
from workflows.wiki_synthesis.extract_shared import article_envelope


def _item(text: str) -> IngestItem:
    return IngestItem(
        item_id="medium::https://x.com/a",
        title="A piece on Claude Code",
        date=date(2026, 3, 15),
        text=text,
        source_type="raw_store",
        source_ref="raw_store:content_1",
        author=None,
    )


def test_numbers_the_body_so_a_claim_can_cite_the_unit_it_came_from():
    envelope = article_envelope(_item("Anthropic shipped subagents. It took two weeks."))

    assert "[0] Anthropic shipped subagents." in envelope
    assert "[1] It took two weeks." in envelope


def _cache_key_of(module_path: str, run) -> str | None:
    """Run one extract-time call against a stubbed LLM; return the prompt_cache_key
    it sent."""
    captured = {}

    def fake(messages, *, model, temperature, prompt_cache_key=None):
        captured["key"] = prompt_cache_key
        return LLMCall(content="NONE", model=model, input_tokens=1, output_tokens=1)

    with patch(module_path, side_effect=fake):
        run()
    return captured["key"]


def test_the_two_extract_calls_declare_the_same_prompt_cache_key():
    # They send a byte-identical [system, envelope] prefix, so the second can be
    # served from the first's cache — but only if routing lands them together, which
    # is what a shared key asks for. Divergent keys would halve the hit rate with no
    # other visible symptom, hence pinning it.
    item = _item("Anthropic shipped subagents. It took two weeks.")

    claims_key = _cache_key_of(
        "workflows.wiki_synthesis.extract_claims.generate_messages_with_usage",
        lambda: extract_claims(item),
    )
    entities_key = _cache_key_of(
        "workflows.wiki_synthesis.extract_entities.generate_messages_with_usage",
        lambda: extract_entities(
            item, ClaimSet(item_id=item.item_id, content_date=None, claims=[])
        ),
    )

    assert claims_key is not None
    assert claims_key == entities_key
