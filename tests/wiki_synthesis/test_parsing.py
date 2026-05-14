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

    def test_parse_extracts_summary_field(self):
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: concept\n"
            "summary: RAG augments LLM generation with retrieval over a corpus.\n"
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
        assert page.summary == "RAG augments LLM generation with retrieval over a corpus."

    def test_parse_ignores_llm_supplied_aliases_and_num_sources(self):
        """Whitelist: only `summary` is accepted from the LLM. `aliases` and
        `num_sources` are producer-supplied at write time and ignored on parse."""
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: concept\n"
            "summary: RAG augments LLM generation.\n"
            "aliases: [HallucinatedAlias]\n"
            "num_sources: 99\n"
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
        # The WikiPage type has no aliases/num_sources fields — they're write-time
        # parameters on write_page. So the assertion is: parse succeeded, summary
        # was extracted, and no spurious attribute leaked through.
        assert page.summary == "RAG augments LLM generation."
        assert not hasattr(page, "aliases")
        assert not hasattr(page, "num_sources")

    def test_parse_falls_back_to_first_sentence_when_summary_missing(self, caplog):
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: concept\n"
            "---\n"
            "# RAG\n\nRAG is a technique for grounding LLMs in retrieved context. "
            "It has two phases."
        )
        page = parse_llm_page_output(
            raw=raw,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            related=[],
            source_id="c1",
        )
        assert page.summary == "RAG is a technique for grounding LLMs in retrieved context."
        assert "usable summary" in caplog.text

    def test_parse_falls_back_when_summary_empty_string(self):
        raw = (
            "---\n"
            "entity_id: concept__rag\n"
            "title: RAG\n"
            "page_type: concept\n"
            "summary: ''\n"
            "---\n"
            "RAG augments generation. Other stuff."
        )
        page = parse_llm_page_output(
            raw=raw,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            related=[],
            source_id="c1",
        )
        assert page.summary == "RAG augments generation."


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
