"""Parity tests for the parsing helpers.

Same coverage as the original tests/wiki/test_ingest.py — TestSlugFromId,
TestParseLlmPageOutput, TestCheckH2Preservation classes — translated to
the new module location. Behavior must match exactly.
"""

from pathlib import Path

from workflows.wiki_synthesis.parsing import (
    check_h2_preservation,
    parse_llm_page_output,
    slug_from_id,
)


class TestSlugFromId:
    def test_normal(self):
        assert slug_from_id("concept__rag") == "rag"

    def test_no_separator(self):
        assert slug_from_id("rag") == "rag"


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
        page = parse_llm_page_output(
            raw=raw,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            related=[],
            source_id="content_abc",
        )
        assert page.entity_id == "concept__rag"
        assert page.content == "# RAG\n\nBody text."

    def test_falls_back_on_bad_frontmatter(self):
        raw = "Just some text without frontmatter."
        page = parse_llm_page_output(
            raw=raw,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            related=["concept__llm"],
            source_id="c1",
        )
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
        page = parse_llm_page_output(
            raw=raw,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            related=[],
            source_id="c1",
        )
        assert page.entity_id == "concept__rag"
        assert page.page_type == "concept"

    def test_enforces_expected_page_type(self):
        """LLM may return a wrong page_type; we enforce the expected one."""
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: tool\n"
            "---\n"
            "# RAG\n\nBody."
        )
        page = parse_llm_page_output(
            raw=raw,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            related=[],
            source_id="c1",
        )
        assert page.page_type == "concept"


class TestCheckH2Preservation:
    def test_warns_on_dropped_section(self, tmp_path: Path, caplog):
        page_path = tmp_path / "page.md"
        page_path.write_text("## Overview\n\nOld text.\n\n## History\n\nMore.")

        new_content = "## Overview\n\nUpdated text."

        check_h2_preservation(page_path, new_content)
        assert "History" in caplog.text

    def test_no_warning_if_preserved(self, tmp_path: Path, caplog):
        page_path = tmp_path / "page.md"
        page_path.write_text("## Overview\n\nOld.\n\n## History\n\nMore.")

        new_content = "## Overview\n\nUpdated.\n\n## History\n\nMore.\n\n## New Section\n\nExtra."

        check_h2_preservation(page_path, new_content)
        assert "dropped" not in caplog.text.lower()
