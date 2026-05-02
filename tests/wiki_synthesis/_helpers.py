"""Shared helpers for wiki_synthesis tests — NOT fixtures.

Plain utility functions for building IngestItem / ExtractionResult /
synthesis-output strings. Imported explicitly from each test module
(unlike conftest.py fixtures which auto-discover). Parity tests in
test_graph.py and test_runner.py share these with PR 2's new-capability
tests so we're not duplicating the same factories four times.
"""

import re
from datetime import date

from domains.wiki.sources import IngestItem
from domains.wiki.types import ExtractedEntity, ExtractionResult


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


def make_extraction(*entity_ids: str, page_type: str = "concept") -> ExtractionResult:
    """Build an ExtractionResult with one ExtractedEntity per id.

    Each id should be in the canonical {page_type}__{slug} form. Title is
    derived from the slug; is_new defaults to True.
    """
    entities = [
        ExtractedEntity(
            entity_id=eid,
            title=eid.split("__", 1)[-1].replace("_", " ").title(),
            page_type=page_type,
            is_new=True,
        )
        for eid in entity_ids
    ]
    return ExtractionResult(entities=entities)


def build_synthesis_output(entity_id: str, *, page_type: str = "concept") -> str:
    """Construct a frontmatter-valid synthesis LLM output for one entity."""
    title = entity_id.split("__", 1)[-1].replace("_", " ").title()
    return (
        "---\n"
        f"entity_id: {entity_id}\n"
        f"title: {title}\n"
        f"page_type: {page_type}\n"
        "---\n"
        f"# {title}\n\nBody."
    )


_ENTITY_ID_LINE = re.compile(r"^entity_id:\s*(\S+)\s*$", re.MULTILINE)


def extract_entity_id_from_prompt(prompt: str) -> str:
    """Pull the prompt's own entity_id from the rendered synthesis prompt.

    Substring-match on the entity_id line specifically (NOT anywhere in the
    prompt) — the prompt also includes a `related entities from this article:`
    list which would create false positives if we matched on the bare id.
    Returns the first match; raises if there is none.
    """
    m = _ENTITY_ID_LINE.search(prompt)
    if not m:
        raise AssertionError(f"could not find entity_id line in prompt:\n{prompt[:300]}")
    return m.group(1)
