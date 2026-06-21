"""num_sources is the deterministic recurrence signal — COUNT(DISTINCT
item_id) from the wiki.page_sources ledger, written by commit. It must NOT
be derived from the LLM-authored sources frontmatter (which rendered 0 on
every fresh single-source page). Drives the full graph with mocked LLMs and
a real Postgres fixture.
"""

from pathlib import Path
from unittest.mock import patch

from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph

from tests.wiki_synthesis._helpers import make_extraction, make_item, make_llm_call


def _invoke(item, *, wiki_dir, db_url):
    graph = build_wiki_synthesis_graph().compile()
    return graph.invoke({"item": item, "db_url": db_url, "wiki_dir": str(wiki_dir)})


def _synthesis_output(entity_id: str, title: str) -> str:
    # The LLM echoes the current source into its frontmatter — exactly the
    # case that made the old `+1 if item not in new_page.sources` skip and
    # render num_sources: 0.
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


def test_fresh_single_source_page_counts_one(tmp_path: Path, wiki_pg, wiki_pg_url):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured_with_usage",
            return_value=(make_extraction("concept__rag"), make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output("concept__rag", "Rag")),
        ),
    ):
        _invoke(make_item(), wiki_dir=wiki_dir, db_url=wiki_pg_url)

    page_md = (wiki_dir / "concept" / "rag.md").read_text(encoding="utf-8")
    assert "num_sources: 1" in page_md


def test_second_distinct_item_counts_two(tmp_path: Path, wiki_pg, wiki_pg_url):
    """The recurrence signal: a second article surfacing the same entity
    bumps num_sources to 2 — even though each LLM output lists only its own
    source. The count comes from the ledger, not the frontmatter."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    def run(item):
        with (
            patch(
                "workflows.wiki_synthesis.nodes.generate_structured_with_usage",
                return_value=(make_extraction("concept__rag"), make_llm_call(model="gpt-4.1-nano")),
            ),
            patch(
                "workflows.wiki_synthesis.entity_graph.generate_with_usage",
                return_value=make_llm_call(content=_synthesis_output("concept__rag", "Rag")),
            ),
        ):
            _invoke(item, wiki_dir=wiki_dir, db_url=wiki_pg_url)

    run(make_item(item_id="content_abc", source_ref="raw_store:content_abc"))
    run(make_item(item_id="content_def", source_ref="raw_store:content_def"))

    page_md = (wiki_dir / "concept" / "rag.md").read_text(encoding="utf-8")
    assert "num_sources: 2" in page_md
