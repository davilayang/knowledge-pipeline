from datetime import date

import pytest
from pydantic import ValidationError

from knowledge_pipeline.lib.wiki.types import (
    ExtractionResult,
    ExtractedEntity,
    WikiPage,
)


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
    entity = ExtractedEntity(
        entity_id="concept__rag",
        title="RAG",
        page_type="concept",
        is_new=False,
        aliases=[],
    )
    assert entity.is_new is False


def test_extraction_result_max_length():
    entities = [
        ExtractedEntity(
            entity_id=f"concept__e{i}",
            title=f"Entity {i}",
            page_type="concept",
            is_new=True,
        )
        for i in range(11)
    ]
    with pytest.raises(ValidationError, match="List should have at most 10 items"):
        ExtractionResult(entities=entities)


def test_extraction_result_within_limit():
    entities = [
        ExtractedEntity(
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            is_new=False,
        )
    ]
    result = ExtractionResult(entities=entities)
    assert len(result.entities) == 1
