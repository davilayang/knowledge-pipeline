"""num_sources is the deterministic recurrence signal — COUNT(DISTINCT item_id)
from the page_sources ledger, written by persist. It must NOT be derived from
the LLM-authored sources frontmatter (which rendered 0 on every fresh
single-source page). Drives synthesize_item with mocked LLMs and a real wiki.db.
"""

from pathlib import Path
from unittest.mock import patch

from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import make_extraction, make_item, make_llm_call


def _synthesis_output(entity_id: str, title: str) -> str:
    # The LLM echoes the current source into its frontmatter — exactly the case
    # that made the old `+1 if item not in new_page.sources` skip and render
    # num_sources: 0.
    return (
        "---\n"
        f"entity_id: {entity_id}\n"
        f"title: {title}\n"
        "page_type: concept\n"
        "related: []\n"
        "sources: [content_abc]\n"
        "---\n"
        f"# {title}\n\nBody."
    )


def test_fresh_single_source_page_counts_one(tmp_path: Path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(make_extraction("concept__rag"), make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output("concept__rag", "Rag")),
        ),
    ):
        synthesize_item(make_item(), db_path=wiki_db_path, wiki_dir=wiki_dir)

    page_md = (wiki_dir / "concept" / "rag.md").read_text(encoding="utf-8")
    assert "num_sources: 1" in page_md


def test_second_distinct_item_counts_two(tmp_path: Path, wiki_db_path):
    """A second article surfacing the same entity bumps num_sources to 2 — even
    though each LLM output lists only its own source. The count comes from the
    ledger, not the frontmatter."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    def run(item):
        with (
            patch(
                "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
                return_value=(make_extraction("concept__rag"), make_llm_call(model="gpt-4.1-nano")),
            ),
            patch(
                "workflows.wiki_synthesis.synthesize.generate_with_usage",
                return_value=make_llm_call(content=_synthesis_output("concept__rag", "Rag")),
            ),
        ):
            synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)

    run(make_item(item_id="content_abc", source_ref="raw_store:content_abc"))
    run(make_item(item_id="content_def", source_ref="raw_store:content_def"))

    page_md = (wiki_dir / "concept" / "rag.md").read_text(encoding="utf-8")
    assert "num_sources: 2" in page_md
