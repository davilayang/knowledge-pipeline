"""extract_shared.article_envelope — the article block both extract-time calls send.

The envelope numbers the body into citable units so a claim can cite the unit
it came from, and stays byte-identical between the claims and entities calls so
the article prompt-caches across the two.
"""

from datetime import date

from domains.types import IngestItem
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
