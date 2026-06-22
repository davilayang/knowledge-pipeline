"""W2.5 Part B — deterministic entity rejection list (denylist).

Denylisted entity_ids are filtered out at extraction time so synthesis never
builds or updates a page for them. Tests drive the public interface
(synthesize_item) with the LLM calls mocked and a real wiki.db.
"""

from pathlib import Path
from unittest.mock import patch

from domains.wiki.state import connect, get_page, get_processed_ids, snapshot_aliases
from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import make_item, make_llm_call


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


def test_denylisted_entity_gets_no_page(tmp_path: Path, wiki_db_path):
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
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output("concept__rag", "RAG")),
        ),
    ):
        synthesize_item(
            make_item(),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"tool__cli"}),
        )

    conn = connect(wiki_db_path)
    try:
        assert get_page(conn, "tool__cli") is None
        assert not (wiki_dir / "tool" / "cli.md").exists()
        assert get_page(conn, "concept__rag") is not None
        assert get_processed_ids(conn, status="ok") == {"content_abc"}
    finally:
        conn.close()


def test_all_denylisted_commits_skipped(tmp_path: Path, wiki_db_path):
    """Every extracted entity rejected → 'skipped', not 'error'."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(entity_id="tool__cli", title="CLI", page_type="tool", is_new=True),
            ExtractedEntity(entity_id="tool__api", title="API", page_type="tool", is_new=True),
        ]
    )

    with patch(
        "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
        return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
    ):
        synthesize_item(
            make_item(),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"tool__cli", "tool__api"}),
        )

    assert list(wiki_dir.glob("**/*.md")) == []
    conn = connect(wiki_db_path)
    try:
        assert get_processed_ids(conn, status="skipped") == {"content_abc"}
        assert get_processed_ids(conn, status="error") == set()
        assert get_processed_ids(conn, status="ok") == set()
    finally:
        conn.close()


def test_denylisted_new_entity_leaves_no_alias(tmp_path: Path, wiki_db_path):
    """A rejected is_new entity must not persist its aliases."""
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
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=_synthesis_output("concept__rag", "RAG")),
        ),
    ):
        synthesize_item(
            make_item(),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"tool__cli"}),
        )

    conn = connect(wiki_db_path)
    try:
        entries = snapshot_aliases(conn).entries
    finally:
        conn.close()
    assert "tool__cli" not in entries
    assert "concept__rag" in entries
