"""Shared helpers for wiki_synthesis tests — NOT fixtures.

Plain utility functions for building IngestItem / ExtractionResult /
synthesis-output strings. Imported explicitly from each test module
(unlike conftest.py fixtures which auto-discover).

The LLM proposes a display name + optional matched_id (never an id); the
resolver mints the surrogate. So these factories take display NAMES, and the
synthesis-output frontmatter id is a placeholder (parse overwrites it with the
caller's surrogate).
"""

from datetime import date

from domains.types import IngestItem
from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.llm import LLMCall


def make_llm_call(
    content: str = "", model: str = "gpt-4.1-mini", *, input_tokens: int = 0, output_tokens: int = 0
) -> LLMCall:
    """Build an LLMCall for mocking *_with_usage helpers in tests."""
    return LLMCall(
        content=content,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def make_item(**overrides) -> IngestItem:
    """Build an IngestItem with sensible test defaults; override any field."""
    defaults = {
        "item_id": "content_abc",
        "title": "Test Article",
        "date": date(2026, 5, 1),
        "text": "# Test\n\nA test article body.",
        "source_type": "raw_store",
        "source_ref": "raw_store:content_abc",
    }
    defaults.update(overrides)
    return IngestItem(**defaults)


def make_extraction(*names: str, page_type: str = "concept") -> ExtractionResult:
    """Build an ExtractionResult with one ExtractedEntity per display name.

    matched_id defaults to None (genuinely new entity → the resolver mints).
    """
    entities = [ExtractedEntity(title=name, page_type=page_type) for name in names]
    return ExtractionResult(entities=entities)


def build_synthesis_output(title: str, *, page_type: str = "concept") -> str:
    """Construct a frontmatter-valid synthesis LLM output for one entity.

    The frontmatter entity_id is a placeholder — parse_llm_page_output overwrites
    it with the caller-supplied surrogate, so its value is irrelevant.
    """
    return (
        "---\n"
        "entity_id: e_placeholder\n"
        f"title: {title}\n"
        f"page_type: {page_type}\n"
        "---\n"
        f"# {title}\n\nBody."
    )
