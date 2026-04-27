from datetime import date
from pathlib import Path
from unittest.mock import patch

from knowledge_pipeline.lib.wiki.aliases import save_aliases, AliasStore
from knowledge_pipeline.lib.wiki.ingest import (
    _check_h2_preservation,
    _parse_llm_page_output,
    _slug_from_id,
    _stage_alias_updates,
    ingest_article,
)
from knowledge_pipeline.lib.wiki.io import write_page
from knowledge_pipeline.lib.wiki.sources import IngestItem
from knowledge_pipeline.lib.wiki.state import WikiStateDB
from knowledge_pipeline.lib.wiki.types import (
    ExtractionResult,
    ExtractedEntity,
    WikiPage,
)


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


class TestSlugFromId:
    def test_normal(self):
        assert _slug_from_id("concept__rag") == "rag"

    def test_no_separator(self):
        assert _slug_from_id("rag") == "rag"


class TestParseLlmPageOutput:
    def test_parses_valid_frontmatter(self):
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: concept\n"
            "related: [concept__llm]\n"
            "sources: [content_abc]\n"
            "---\n"
            "# RAG\n\nBody text."
        )
        page = _parse_llm_page_output(raw, "concept__rag", "RAG", "concept", [], "content_abc")
        assert page.entity_id == "concept__rag"
        assert page.content == "# RAG\n\nBody text."

    def test_falls_back_on_bad_frontmatter(self):
        raw = "Just some text without frontmatter."
        page = _parse_llm_page_output(raw, "concept__rag", "RAG", "concept", ["concept__llm"], "c1")
        assert page.entity_id == "concept__rag"
        assert page.content == raw
        assert page.related == ["concept__llm"]
        assert page.sources == ["c1"]

    def test_enforces_expected_entity_id(self):
        """LLM may hallucinate a different entity_id; we enforce the expected one."""
        raw = (
            "---\n"
            "entity_id: concept__wrong_id\n"
            "title: RAG\n"
            "page_type: concept\n"
            "---\n"
            "# RAG\n\nBody."
        )
        page = _parse_llm_page_output(raw, "concept__rag", "RAG", "concept", [], "c1")
        assert page.entity_id == "concept__rag"
        assert page.page_type == "concept"

    def test_enforces_expected_page_type(self):
        """LLM may return wrong page_type; we enforce the expected one."""
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: tool\n"
            "---\n"
            "# RAG\n\nBody."
        )
        page = _parse_llm_page_output(raw, "concept__rag", "RAG", "concept", [], "c1")
        assert page.page_type == "concept"


class TestStageAliasUpdates:
    def test_stages_new_entities(self):
        store = AliasStore()
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
        staged = _stage_alias_updates(store, extraction)
        assert len(staged) == 1
        assert staged[0] == ("concept__rag", "RAG", ["Retrieval-Augmented Generation"])

    def test_skips_existing_entities(self):
        store = AliasStore()
        store.add("concept__rag", "RAG")
        extraction = ExtractionResult(
            entities=[
                ExtractedEntity(
                    entity_id="concept__rag",
                    title="RAG",
                    page_type="concept",
                    is_new=True,
                )
            ]
        )
        staged = _stage_alias_updates(store, extraction)
        assert len(staged) == 0

    def test_skips_non_new(self):
        store = AliasStore()
        extraction = ExtractionResult(
            entities=[
                ExtractedEntity(
                    entity_id="concept__rag",
                    title="RAG",
                    page_type="concept",
                    is_new=False,
                )
            ]
        )
        staged = _stage_alias_updates(store, extraction)
        assert len(staged) == 0


class TestCheckH2Preservation:
    def test_warns_on_dropped_section(self, tmp_path: Path, caplog):
        page_path = tmp_path / "page.md"
        page_path.write_text("## Overview\n\nOld text.\n\n## History\n\nMore.")

        new_content = "## Overview\n\nUpdated text."

        _check_h2_preservation(page_path, new_content)
        assert "History" in caplog.text

    def test_no_warning_if_preserved(self, tmp_path: Path, caplog):
        page_path = tmp_path / "page.md"
        page_path.write_text("## Overview\n\nOld.\n\n## History\n\nMore.")

        new_content = "## Overview\n\nUpdated.\n\n## History\n\nMore.\n\n## New Section\n\nExtra."

        _check_h2_preservation(page_path, new_content)
        assert "dropped" not in caplog.text.lower()


class TestIngestArticle:
    def test_creates_new_page(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        aliases_path = tmp_path / "aliases.yaml"
        state_db = WikiStateDB(tmp_path / "state.db")

        mock_extraction = ExtractionResult(
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

        mock_llm_output = (
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
                "knowledge_pipeline.lib.wiki.ingest.generate_structured",
                return_value=mock_extraction,
            ),
            patch(
                "knowledge_pipeline.lib.wiki.ingest.generate",
                return_value=mock_llm_output,
            ),
        ):
            pages = ingest_article(
                _make_item(),
                wiki_dir=wiki_dir,
                aliases_path=aliases_path,
                state_db=state_db,
            )

        assert pages == ["concept__rag"]
        assert (wiki_dir / "concept" / "rag.md").exists()

        # Aliases should be persisted
        assert aliases_path.exists()

        # State DB should have the page
        page_record = state_db.get_page("concept__rag")
        assert page_record is not None
        assert page_record.page_type == "concept"
        state_db.close()

    def test_no_entities_returns_empty(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        aliases_path = tmp_path / "aliases.yaml"
        state_db = WikiStateDB(tmp_path / "state.db")

        mock_extraction = ExtractionResult(entities=[])

        with patch(
            "knowledge_pipeline.lib.wiki.ingest.generate_structured",
            return_value=mock_extraction,
        ):
            pages = ingest_article(
                _make_item(),
                wiki_dir=wiki_dir,
                aliases_path=aliases_path,
                state_db=state_db,
            )

        assert pages == []
        state_db.close()

    def test_failed_synthesis_continues(self, tmp_path: Path):
        """If one page synthesis fails, others should still succeed."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        aliases_path = tmp_path / "aliases.yaml"
        state_db = WikiStateDB(tmp_path / "state.db")

        mock_extraction = ExtractionResult(
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

        call_count = 0

        def mock_generate(prompt, *, system="", model=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM timeout")
            return (
                "---\n"
                "entity_id: tool__chromadb\n"
                "title: ChromaDB\n"
                "page_type: tool\n"
                "related: []\n"
                "sources: [content_abc]\n"
                "---\n"
                "# ChromaDB\n\nVector database."
            )

        with (
            patch(
                "knowledge_pipeline.lib.wiki.ingest.generate_structured",
                return_value=mock_extraction,
            ),
            patch(
                "knowledge_pipeline.lib.wiki.ingest.generate",
                side_effect=mock_generate,
            ),
        ):
            pages = ingest_article(
                _make_item(),
                wiki_dir=wiki_dir,
                aliases_path=aliases_path,
                state_db=state_db,
            )

        # Only ChromaDB should succeed
        assert pages == ["tool__chromadb"]
        assert (wiki_dir / "tool" / "chromadb.md").exists()
        assert not (wiki_dir / "concept" / "rag.md").exists()

        # Aliases should still be persisted (ChromaDB succeeded)
        assert aliases_path.exists()
        state_db.close()

    def test_all_synthesis_fails_no_aliases_persisted(self, tmp_path: Path):
        """If all page syntheses fail, aliases should NOT be persisted."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        aliases_path = tmp_path / "aliases.yaml"
        state_db = WikiStateDB(tmp_path / "state.db")

        mock_extraction = ExtractionResult(
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
                "knowledge_pipeline.lib.wiki.ingest.generate_structured",
                return_value=mock_extraction,
            ),
            patch(
                "knowledge_pipeline.lib.wiki.ingest.generate",
                side_effect=RuntimeError("LLM timeout"),
            ),
        ):
            pages = ingest_article(
                _make_item(),
                wiki_dir=wiki_dir,
                aliases_path=aliases_path,
                state_db=state_db,
            )

        assert pages == []
        assert not aliases_path.exists()
        state_db.close()
