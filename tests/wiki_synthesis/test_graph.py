"""End-to-end parity tests for the wiki_synthesis LangGraph workflow.

Exercises the full graph: extract_entities → fan_out (Send per entity) →
process_entity sub-graph → commit. LLM calls are mocked at their import
locations; Postgres is the real pytest-postgresql fixture so transactional
behavior is genuinely tested.

Maps onto the legacy tests/wiki/test_ingest.py::TestIngestArticle suite —
each test below corresponds to one in the legacy suite plus one new test
for the I1 fix (extract failure writes a status='error' processed row
instead of leaving no DB footprint).
"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from domains.wiki.sources import IngestItem
from domains.wiki.state import (
    get_failed,
    get_page,
    get_processed_ids,
    snapshot_aliases,
)
from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph


def _make_item(**overrides) -> IngestItem:
    defaults = {
        "item_id": "content_abc",
        "title": "RAG is All You Need",
        "date": date(2026, 4, 1),
        "text": "# RAG\n\nRAG is a technique for augmenting LLM generation.",
        "source_type": "raw_store",
        "source_ref": "raw_store:content_abc",
    }
    defaults.update(overrides)
    return IngestItem(**defaults)


def _invoke(item, *, wiki_dir, db_url):
    """Compile the graph (no checkpointer) and run it on one item."""
    graph = build_wiki_synthesis_graph().compile()
    return graph.invoke(
        {"item": item, "db_url": db_url, "wiki_dir": str(wiki_dir)},
    )


def test_creates_new_page(tmp_path: Path, wiki_pg, wiki_pg_url):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(
                entity_id="concept__rag",
                title="RAG",
                page_type="concept",
                is_new=True,
                aliases=["Retrieval-Augmented Generation"],
            )
        ]
    )
    llm_output = (
        "---\n"
        "entity_id: concept__rag\n"
        "title: RAG\n"
        "page_type: concept\n"
        "related: []\n"
        "sources: [content_abc]\n"
        "---\n"
        "# RAG\n\nRAG is a technique."
    )

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            return_value=llm_output,
        ),
    ):
        _invoke(_make_item(), wiki_dir=wiki_dir, db_url=wiki_pg_url)

    assert (wiki_dir / "concept" / "rag.md").exists()
    assert get_processed_ids(wiki_pg, status="ok") == {"content_abc"}

    page = get_page(wiki_pg, "concept__rag")
    assert page is not None
    assert page.page_type == "concept"
    assert page.file_path == "concept/rag.md"

    store = snapshot_aliases(wiki_pg)
    assert "concept__rag" in store.entries
    assert "Retrieval-Augmented Generation" in store.entries["concept__rag"].aliases


def test_no_entities_returns_skipped(tmp_path: Path, wiki_pg, wiki_pg_url):
    """Legacy returned []. New workflow writes a status='skipped' processed row
    so Dagster can see the item was attempted (vs the old 'silent no-op')."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(entities=[])

    with patch("workflows.wiki_synthesis.nodes.generate_structured", return_value=extraction):
        _invoke(_make_item(), wiki_dir=wiki_dir, db_url=wiki_pg_url)

    # No pages, no aliases
    assert list(wiki_dir.glob("**/*.md")) == []
    assert snapshot_aliases(wiki_pg).entries == {}

    # But a 'skipped' row is recorded
    assert get_processed_ids(wiki_pg, status="skipped") == {"content_abc"}
    assert get_processed_ids(wiki_pg, status="ok") == set()


def test_failed_synthesis_continues(tmp_path: Path, wiki_pg, wiki_pg_url):
    """If one per-entity sub-graph fails, the others still succeed.

    Same isolation contract as the legacy try/except continue loop, achieved
    here through process_entity's broad try/except returning an error
    EntityResult rather than raising.
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(
                entity_id="concept__rag",
                title="RAG",
                page_type="concept",
                is_new=True,
            ),
            ExtractedEntity(
                entity_id="tool__chromadb",
                title="ChromaDB",
                page_type="tool",
                is_new=True,
            ),
        ]
    )

    chroma_output = (
        "---\n"
        "entity_id: tool__chromadb\n"
        "title: ChromaDB\n"
        "page_type: tool\n"
        "---\n"
        "# ChromaDB\n\nVector database."
    )

    def mock_generate(prompt, *, system="", model=""):
        # Discriminate by the prompt's own entity_id line — substring matches
        # against "concept__rag" alone would also fire for sibling/related lists.
        if "entity_id: concept__rag" in prompt:
            raise RuntimeError("LLM timeout")
        if "entity_id: tool__chromadb" in prompt:
            return chroma_output
        raise AssertionError(f"unexpected prompt:\n{prompt[:200]}")

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=mock_generate,
        ),
    ):
        _invoke(_make_item(), wiki_dir=wiki_dir, db_url=wiki_pg_url)

    # Only ChromaDB succeeded
    assert (wiki_dir / "tool" / "chromadb.md").exists()
    assert not (wiki_dir / "concept" / "rag.md").exists()
    assert get_page(wiki_pg, "tool__chromadb") is not None
    assert get_page(wiki_pg, "concept__rag") is None

    # Status is 'ok' (at least one success) but error column carries the failure
    assert get_processed_ids(wiki_pg, status="ok") == {"content_abc"}
    err_row = wiki_pg.execute(
        "SELECT error FROM wiki.processed WHERE item_id = 'content_abc'"
    ).fetchone()
    assert "concept__rag" in err_row[0]
    assert "LLM timeout" in err_row[0]

    # Aliases were persisted (at least one page succeeded)
    store = snapshot_aliases(wiki_pg)
    assert "tool__chromadb" in store.entries
    assert "concept__rag" in store.entries  # both staged before any synthesis ran


def test_all_synthesis_fails_no_aliases_persisted(tmp_path: Path, wiki_pg, wiki_pg_url):
    """If every per-entity sub-graph fails, aliases are NOT persisted and the
    processed row goes in as status='error'."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(
                entity_id="concept__rag",
                title="RAG",
                page_type="concept",
                is_new=True,
                aliases=["Retrieval-Augmented Generation"],
            )
        ]
    )

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=RuntimeError("LLM timeout"),
        ),
    ):
        _invoke(_make_item(), wiki_dir=wiki_dir, db_url=wiki_pg_url)

    # No pages
    assert list(wiki_dir.glob("**/*.md")) == []
    assert get_page(wiki_pg, "concept__rag") is None

    # No aliases persisted
    assert snapshot_aliases(wiki_pg).entries == {}

    # Failure row recorded
    assert get_processed_ids(wiki_pg, status="error") == {"content_abc"}
    failed = get_failed(wiki_pg)
    assert len(failed) == 1
    assert "concept__rag" in failed[0].error


def test_extract_failure_writes_error_row(tmp_path: Path, wiki_pg, wiki_pg_url):
    """New behavior (I1 fix): if the extraction LLM call itself raises, the
    workflow still reaches commit and writes a status='error' processed row.

    Without this fix, hard extraction failures would leave no DB footprint
    and Dagster would keep retrying the same item forever.
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    with patch(
        "workflows.wiki_synthesis.nodes.generate_structured",
        side_effect=RuntimeError("OpenAI 503"),
    ):
        _invoke(_make_item(), wiki_dir=wiki_dir, db_url=wiki_pg_url)

    assert get_processed_ids(wiki_pg, status="error") == {"content_abc"}
    failed = get_failed(wiki_pg)
    assert len(failed) == 1
    assert "OpenAI 503" in failed[0].error
