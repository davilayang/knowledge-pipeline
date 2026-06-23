"""Tests for domains.wiki.identity.page_content_hash — the semantic-content
hash that gates wiki page version appends (#47)."""

from datetime import date

from domains.wiki.identity import page_content_hash
from domains.wiki.types import WikiPage


def _page(**overrides) -> WikiPage:
    base = dict(
        entity_id="e_abc123",
        title="Retrieval-Augmented Generation",
        page_type="concept",
        summary="RAG grounds an LLM in retrieved documents.",
        related=["e_def456"],
        sources=["content_1"],
        updated_at=date(2026, 6, 23),
        content="# RAG\n\nBody text.",
    )
    base.update(overrides)
    return WikiPage(**base)


def test_hash_ignores_updated_at():
    """A re-synthesis that changes only the timestamp is not a new edition —
    the hash gate must treat the two as identical so no version is appended."""
    a = _page(updated_at=date(2026, 6, 23))
    b = _page(updated_at=date(2026, 7, 1))
    assert page_content_hash(a) == page_content_hash(b)


def test_hash_changes_when_body_changes():
    """The body is semantic content — an edited body is a new edition."""
    assert page_content_hash(_page(content="v1")) != page_content_hash(_page(content="v2"))


def test_hash_changes_when_summary_changes():
    assert page_content_hash(_page(summary="a")) != page_content_hash(_page(summary="b"))


def test_hash_ignores_per_item_volatile_fields():
    """`sources` defaults to the single triggering item id and `related` is the
    triggering article's co-extracted siblings — both per-run noise, not the
    page's accumulated state. A different article re-synthesising identical prose
    must NOT fork a new edition (accumulated provenance lives in the ledger)."""
    base = _page(related=["e_1"], sources=["c_1"])
    assert page_content_hash(_page(related=["e_2"], sources=["c_1"])) == page_content_hash(base)
    assert page_content_hash(_page(related=["e_1"], sources=["c_2"])) == page_content_hash(base)
    assert page_content_hash(_page(related=["e_9"], sources=["c_9"])) == page_content_hash(base)


def test_hash_ignores_non_semantic_identity_fields():
    """title / page_type / entity_id are not semantic content — a rename or
    retype is not a new edition (they live on `entities`, not the body)."""
    base = _page()
    assert page_content_hash(_page(title="Renamed")) == page_content_hash(base)
    assert page_content_hash(_page(page_type="tool")) == page_content_hash(base)
    assert page_content_hash(_page(entity_id="e_other")) == page_content_hash(base)
