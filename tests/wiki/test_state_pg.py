"""Tests for domains.wiki.state — the Postgres helpers.

Uses the `wiki_pg` fixture from tests/conftest.py: a fresh psycopg
connection to a temp Postgres with the wiki schema loaded.
"""

from datetime import date

from domains.wiki.state import (
    count_sources_for_entity,
    get_aliases_for_entity,
    get_all_pages,
    get_failed,
    get_page,
    get_processed_ids,
    insert_aliases_idempotent,
    insert_page_source,
    insert_processed,
    is_source_for_entity,
    snapshot_aliases,
    upsert_page,
)
from domains.wiki.types import WikiPage


def _make_page(entity_id: str = "concept__rag", **overrides) -> WikiPage:
    defaults = {
        "entity_id": entity_id,
        "title": "RAG",
        "page_type": "concept",
        "related": [],
        "sources": ["content_abc"],
        "updated_at": date(2026, 5, 1),
        "content": "# RAG\n\nBody.",
    }
    defaults.update(overrides)
    return WikiPage(**defaults)


# --- wiki.processed ---


def test_insert_processed_inserts_new_row(wiki_pg):
    insert_processed(wiki_pg, item_id="content_abc", source_type="raw_store", status="ok")
    wiki_pg.commit()
    assert get_processed_ids(wiki_pg, status="ok") == {"content_abc"}


def test_insert_processed_upserts_on_conflict(wiki_pg):
    insert_processed(
        wiki_pg,
        item_id="content_abc",
        source_type="raw_store",
        status="error",
        error="boom",
    )
    insert_processed(
        wiki_pg,
        item_id="content_abc",
        source_type="raw_store",
        status="ok",
    )
    wiki_pg.commit()
    assert get_processed_ids(wiki_pg, status="ok") == {"content_abc"}
    assert get_processed_ids(wiki_pg, status="error") == set()


def test_insert_processed_separate_source_types_coexist(wiki_pg):
    """item_id alone is not unique; (item_id, source_type) is the PK."""
    insert_processed(wiki_pg, item_id="abc", source_type="raw_store", status="ok")
    insert_processed(wiki_pg, item_id="abc", source_type="local_file", status="ok")
    wiki_pg.commit()
    rows = wiki_pg.execute(
        "SELECT source_type FROM wiki.processed WHERE item_id = 'abc' ORDER BY source_type"
    ).fetchall()
    assert [r[0] for r in rows] == ["local_file", "raw_store"]


def test_get_failed_returns_only_errors(wiki_pg):
    insert_processed(wiki_pg, item_id="a", source_type="raw_store", status="ok")
    insert_processed(wiki_pg, item_id="b", source_type="raw_store", status="error", error="oops")
    wiki_pg.commit()
    failed = get_failed(wiki_pg)
    assert len(failed) == 1
    assert failed[0].item_id == "b"
    assert failed[0].error == "oops"


# --- wiki.pages ---


def test_upsert_page_inserts_then_updates(wiki_pg):
    page = _make_page()
    upsert_page(wiki_pg, page=page, file_path="concept/rag.md", source_types=["raw_store"])
    wiki_pg.commit()

    rec = get_page(wiki_pg, "concept__rag")
    assert rec is not None
    assert rec.file_path == "concept/rag.md"
    assert rec.source_types == ["raw_store"]

    # Update — same entity_id, different file_path + add a new source_type
    page2 = _make_page(related=["concept__llm"])
    upsert_page(
        wiki_pg, page=page2, file_path="concept/rag.md", source_types=["raw_store", "local_file"]
    )
    wiki_pg.commit()

    rec2 = get_page(wiki_pg, "concept__rag")
    assert rec2.related == ["concept__llm"]
    assert rec2.source_types == ["raw_store", "local_file"]


def test_get_all_pages_orders_by_entity_id(wiki_pg):
    upsert_page(
        wiki_pg,
        page=_make_page("tool__chroma", page_type="tool"),
        file_path="tool/chroma.md",
        source_types=["raw_store"],
    )
    upsert_page(
        wiki_pg,
        page=_make_page("concept__rag"),
        file_path="concept/rag.md",
        source_types=["raw_store"],
    )
    wiki_pg.commit()

    pages = get_all_pages(wiki_pg)
    assert [p.entity_id for p in pages] == ["concept__rag", "tool__chroma"]


# --- wiki.aliases ---


def test_insert_aliases_idempotent_writes_canonical_and_aliases(wiki_pg):
    insert_aliases_idempotent(
        wiki_pg,
        [("concept__rag", "RAG", ["Retrieval-Augmented Generation", "Retrieval Augmented"])],
    )
    wiki_pg.commit()

    store = snapshot_aliases(wiki_pg)
    assert "concept__rag" in store.entries
    entry = store.entries["concept__rag"]
    assert entry.canonical == "RAG"
    assert set(entry.aliases) == {"Retrieval-Augmented Generation", "Retrieval Augmented"}


def test_insert_aliases_skips_duplicate_alias(wiki_pg):
    """Re-inserting an existing alias is a no-op (ON CONFLICT DO NOTHING)."""
    insert_aliases_idempotent(wiki_pg, [("concept__rag", "RAG", [])])
    insert_aliases_idempotent(wiki_pg, [("concept__rag", "RAG", [])])
    wiki_pg.commit()

    rows = wiki_pg.execute("SELECT count(*) FROM wiki.aliases").fetchone()
    assert rows[0] == 1


def test_insert_aliases_concurrent_alias_collision_first_wins(wiki_pg):
    """If two entities try to claim the same alias, the first writer wins."""
    insert_aliases_idempotent(wiki_pg, [("concept__rag", "RAG", ["RA"])])
    insert_aliases_idempotent(wiki_pg, [("tool__rust_analyzer", "Rust Analyzer", ["RA"])])
    wiki_pg.commit()

    row = wiki_pg.execute("SELECT entity_id FROM wiki.aliases WHERE alias = 'RA'").fetchone()
    assert row[0] == "concept__rag"


def test_snapshot_aliases_empty_returns_empty_store(wiki_pg):
    store = snapshot_aliases(wiki_pg)
    assert store.entries == {}


def test_insert_aliases_idempotent_no_entries_is_noop(wiki_pg):
    insert_aliases_idempotent(wiki_pg, [])
    wiki_pg.commit()
    assert wiki_pg.execute("SELECT count(*) FROM wiki.aliases").fetchone()[0] == 0


# --- bridge helpers: get_aliases_for_entity / count_sources_for_entity ---


def test_get_aliases_for_entity_returns_sorted_list(wiki_pg):
    insert_aliases_idempotent(
        wiki_pg,
        [("concept__rag", "RAG", ["Retrieval-Augmented Generation", "Retrieval Augmented"])],
    )
    wiki_pg.commit()
    aliases = get_aliases_for_entity(wiki_pg, "concept__rag")
    assert aliases == sorted(aliases)
    assert set(aliases) == {"RAG", "Retrieval Augmented", "Retrieval-Augmented Generation"}


def test_get_aliases_for_entity_unknown_returns_empty(wiki_pg):
    assert get_aliases_for_entity(wiki_pg, "concept__missing") == []


def test_count_sources_for_entity_counts_distinct_items(wiki_pg):
    """Counts distinct item_ids in the wiki.page_sources ledger (the
    deterministic record of which item contributed which entity) — no longer
    derived from the LLM-authored wiki.pages.sources jsonb array."""
    insert_page_source(
        wiki_pg, entity_id="concept__rag", item_id="content_a", source_type="raw_store"
    )
    insert_page_source(
        wiki_pg, entity_id="concept__rag", item_id="content_b", source_type="raw_store"
    )
    # Re-inserting the same edge is idempotent — still one distinct contribution.
    insert_page_source(
        wiki_pg, entity_id="concept__rag", item_id="content_a", source_type="raw_store"
    )
    # A different entity's edge must not bleed into this count.
    insert_page_source(
        wiki_pg, entity_id="concept__other", item_id="content_z", source_type="raw_store"
    )
    wiki_pg.commit()

    assert count_sources_for_entity(wiki_pg, "concept__rag") == 2


def test_count_sources_for_entity_unknown_returns_zero(wiki_pg):
    assert count_sources_for_entity(wiki_pg, "concept__missing") == 0


def test_is_source_for_entity_reflects_ledger(wiki_pg):
    insert_page_source(
        wiki_pg, entity_id="concept__rag", item_id="content_a", source_type="raw_store"
    )
    wiki_pg.commit()
    assert is_source_for_entity(wiki_pg, "concept__rag", "content_a") is True
    assert is_source_for_entity(wiki_pg, "concept__rag", "content_b") is False
