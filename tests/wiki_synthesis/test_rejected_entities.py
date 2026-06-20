"""W2.5 Part B — deterministic entity rejection list (denylist).

Denylisted entity_ids are filtered out at extraction time so synthesis
never builds or updates a page for them. Tests drive the full graph
(public interface) with the LLM calls mocked and a real Postgres fixture,
mirroring tests/wiki_synthesis/test_graph.py.
"""

from pathlib import Path
from unittest.mock import patch

from domains.wiki.state import get_page, get_processed_ids, snapshot_aliases
from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph

from tests.wiki_synthesis._helpers import make_item, make_llm_call


def _invoke(item, *, wiki_dir, db_url, rejected_entities=None):
    graph = build_wiki_synthesis_graph().compile()
    state = {"item": item, "db_url": db_url, "wiki_dir": str(wiki_dir)}
    if rejected_entities is not None:
        state["rejected_entities"] = rejected_entities
    return graph.invoke(state)


def _synthesis_output(entity_id: str, title: str) -> str:
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


def test_denylisted_entity_gets_no_page(tmp_path: Path, wiki_pg, wiki_pg_url):
    """An entity_id on the rejection list is never built; a sibling is."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(
                entity_id="concept__rag", title="RAG", page_type="concept", is_new=True
            ),
            ExtractedEntity(entity_id="tool__cli", title="CLI", page_type="tool", is_new=True),
        ]
    )

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output("concept__rag", "RAG")),
        ),
    ):
        _invoke(
            make_item(),
            wiki_dir=wiki_dir,
            db_url=wiki_pg_url,
            rejected_entities={"tool__cli"},
        )

    # The denylisted entity is never built.
    assert get_page(wiki_pg, "tool__cli") is None
    assert not (wiki_dir / "tool" / "cli.md").exists()
    # The sibling still is.
    assert get_page(wiki_pg, "concept__rag") is not None
    assert get_processed_ids(wiki_pg, status="ok") == {"content_abc"}


def test_all_denylisted_commits_skipped(tmp_path: Path, wiki_pg, wiki_pg_url):
    """Every extracted entity rejected → 'skipped', not 'error' (codex pitfall #3)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(entity_id="tool__cli", title="CLI", page_type="tool", is_new=True),
            ExtractedEntity(entity_id="tool__api", title="API", page_type="tool", is_new=True),
        ]
    )

    with patch(
        "workflows.wiki_synthesis.nodes.generate_structured_with_usage",
        return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
    ):
        _invoke(
            make_item(),
            wiki_dir=wiki_dir,
            db_url=wiki_pg_url,
            rejected_entities={"tool__cli", "tool__api"},
        )

    assert list(wiki_dir.glob("**/*.md")) == []
    assert get_processed_ids(wiki_pg, status="skipped") == {"content_abc"}
    assert get_processed_ids(wiki_pg, status="error") == set()
    assert get_processed_ids(wiki_pg, status="ok") == set()


def test_denylisted_new_entity_leaves_no_alias(tmp_path: Path, wiki_pg, wiki_pg_url):
    """A rejected is_new entity must not persist its aliases (codex pitfall #2)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(
                entity_id="concept__rag", title="RAG", page_type="concept", is_new=True
            ),
            ExtractedEntity(
                entity_id="tool__cli",
                title="CLI",
                page_type="tool",
                is_new=True,
                aliases=["command line interface"],
            ),
        ]
    )

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output("concept__rag", "RAG")),
        ),
    ):
        _invoke(
            make_item(),
            wiki_dir=wiki_dir,
            db_url=wiki_pg_url,
            rejected_entities={"tool__cli"},
        )

    entries = snapshot_aliases(wiki_pg).entries
    assert "tool__cli" not in entries
    assert "concept__rag" in entries
