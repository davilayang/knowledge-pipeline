from datetime import date

import pytest
from domains.wiki.types import (
    ExtractedEntity,
    ExtractionResult,
    WikiPage,
)
from pydantic import ValidationError


def test_wiki_page_basic():
    page = WikiPage(
        entity_id="concept__rag",
        title="Retrieval-Augmented Generation",
        page_type="concept",
        related=["concept__vector_db"],
        sources=["content_123"],
        updated_at=date(2026, 4, 21),
        content="# RAG\n\nRAG is a technique...",
    )
    assert page.entity_id == "concept__rag"
    assert page.page_type == "concept"


def test_wiki_page_defaults():
    page = WikiPage(
        entity_id="tool__chromadb",
        title="ChromaDB",
        page_type="tool",
        updated_at=date(2026, 4, 21),
        content="# ChromaDB",
    )
    assert page.related == []
    assert page.sources == []


def test_wiki_page_invalid_page_type():
    with pytest.raises(ValidationError):
        WikiPage(
            entity_id="x__y",
            title="Bad",
            page_type="invalid",
            updated_at=date(2026, 4, 21),
            content="",
        )


def test_extracted_entity():
    entity = ExtractedEntity(title="RAG", page_type="concept")
    # The LLM never mints an id; matched_id defaults to None (genuinely new).
    assert entity.matched_id is None
    assert entity.aliases == []


def test_extracted_entity_carries_matched_id():
    entity = ExtractedEntity(title="the MCP standard", page_type="concept", matched_id="e_abc123")
    assert entity.matched_id == "e_abc123"


def test_extraction_result_max_length():
    entities = [ExtractedEntity(title=f"Entity {i}", page_type="concept") for i in range(11)]
    with pytest.raises(ValidationError, match="List should have at most 10 items"):
        ExtractionResult(entities=entities)


def test_extraction_result_within_limit():
    result = ExtractionResult(entities=[ExtractedEntity(title="RAG", page_type="concept")])
    assert len(result.entities) == 1
