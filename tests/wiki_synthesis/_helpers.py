"""Shared helpers for wiki_synthesis tests — NOT fixtures.

Plain utility functions imported explicitly from each test module (unlike
conftest.py fixtures which auto-discover).
"""

from datetime import date

from domains.types import IngestItem
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
