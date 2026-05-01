"""Parity tests for _stage_alias_updates — the in-process prefilter that
selects which extracted entities should write new alias rows on commit.
Same logic as the legacy ingest's _stage_alias_updates.
"""

from domains.wiki.aliases import AliasStore
from domains.wiki.types import ExtractedEntity
from workflows.wiki_synthesis.nodes import _stage_alias_updates


def test_stages_new_entities():
    store = AliasStore()
    entities = [
        ExtractedEntity(
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            is_new=True,
            aliases=["Retrieval-Augmented Generation"],
        )
    ]
    staged = _stage_alias_updates(store, entities)
    assert staged == [("concept__rag", "RAG", ["Retrieval-Augmented Generation"])]


def test_skips_existing_entities():
    store = AliasStore()
    store.add("concept__rag", "RAG")
    entities = [
        ExtractedEntity(
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            is_new=True,
        )
    ]
    assert _stage_alias_updates(store, entities) == []


def test_skips_non_new():
    store = AliasStore()
    entities = [
        ExtractedEntity(
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            is_new=False,
        )
    ]
    assert _stage_alias_updates(store, entities) == []
