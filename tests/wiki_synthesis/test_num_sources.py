"""num_sources is the deterministic recurrence signal — COUNT(DISTINCT item_id)
from the page_sources ledger, written AFTER persist commits this item's edge. It
must NOT be derived from the LLM-authored sources frontmatter (which rendered 0
on every fresh single-source page). Drives synthesize_item with mocked LLMs and
a real wiki.db.
"""

from pathlib import Path
from unittest.mock import patch

from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import make_item, make_llm_call


def _extraction() -> ExtractionResult:
    return ExtractionResult(entities=[ExtractedEntity(title="RAG", page_type="concept")])


def _synthesis_output() -> str:
    # The LLM echoes the current source into its frontmatter — exactly the case
    # that made the old `+1 if item not in new_page.sources` skip and render
    # num_sources: 0. The ledger, not this frontmatter, must drive the count.
    return (
        "---\n"
        "entity_id: e_placeholder\n"
        "title: RAG\n"
        "page_type: concept\n"
        "related: []\n"
        "sources: [content_abc]\n"
        "---\n"
        "# RAG\n\nBody."
    )


def _only_page(wiki_dir: Path) -> str:
    files = list(wiki_dir.glob("*.md"))
    assert len(files) == 1, f"expected one page, found {files}"
    return files[0].read_text(encoding="utf-8")


def _run(item, wiki_db_path, wiki_dir):
    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(_extraction(), make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output()),
        ),
    ):
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)


def test_fresh_single_source_page_counts_one(tmp_path: Path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run(make_item(), wiki_db_path, wiki_dir)

    assert "num_sources: 1" in _only_page(wiki_dir)


def test_second_distinct_item_counts_two(tmp_path: Path, wiki_db_path):
    """A second article surfacing the same entity bumps num_sources to 2 — even
    though each LLM output lists only its own source. The same entity is reused
    (exact normalised-name match), so the single page updates in place and the
    count comes from the ledger, not the frontmatter."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run(
        make_item(item_id="content_abc", source_ref="raw_store:content_abc"), wiki_db_path, wiki_dir
    )
    _run(
        make_item(item_id="content_def", source_ref="raw_store:content_def"), wiki_db_path, wiki_dir
    )

    assert "num_sources: 2" in _only_page(wiki_dir)
