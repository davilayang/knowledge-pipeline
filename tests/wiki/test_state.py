"""Tests for domains.wiki.state — the SQLite (wiki.db) helpers.

Uses the `wiki_db` fixture from tests/conftest.py: a fresh sqlite3 connection to
a temp wiki.db with the schema applied.

Identity now lives in `entities`; pages/aliases/page_sources FK to it, so every
test that writes one of those seeds its parent entity first (foreign_keys=ON).
"""

import sqlite3

import pytest
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import (
    build_entity_index,
    count_sources_for_entity,
    get_aliases_for_entity,
    get_all_pages,
    get_entity,
    get_failed,
    get_page,
    get_page_history,
    get_page_version,
    get_processed_ids,
    insert_aliases,
    insert_entity,
    insert_page_source,
    insert_page_version,
    insert_processed,
    is_source_for_entity,
    snapshot_aliases,
    upsert_page,
)

NOW = "2026-06-22T00:00:00+00:00"


def _seed_entity(conn, entity_id, canonical, *, page_type="concept") -> EntityRecord:
    ent = EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        page_type=page_type,
        created_at=NOW,
    )
    insert_entity(conn, ent)
    return ent


# --- entities ---


def test_insert_and_get_entity_roundtrips(wiki_db):
    ent = _seed_entity(wiki_db, "e_rag", "RAG")
    wiki_db.commit()
    assert get_entity(wiki_db, "e_rag") == ent


def test_get_entity_unknown_returns_none(wiki_db):
    assert get_entity(wiki_db, "e_missing") is None


def test_insert_entity_idempotent_on_entity_id(wiki_db):
    """Re-inserting the same surrogate is a no-op; the first write is kept."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_rag", "RAG (changed)", page_type="tool")
    wiki_db.commit()
    got = get_entity(wiki_db, "e_rag")
    assert got.canonical_name == "RAG"
    assert got.page_type == "concept"


def test_build_entity_index_includes_names_and_aliases(wiki_db):
    _seed_entity(wiki_db, "e_mcp", "Model Context Protocol")
    insert_aliases(wiki_db, [("MCP", "e_mcp")])
    wiki_db.commit()

    index = build_entity_index(wiki_db)
    assert index.by_normalized_name["model context protocol"] == "e_mcp"
    assert index.by_normalized_alias["mcp"] == "e_mcp"


# --- page_versions ---


def test_insert_and_read_page_version_roundtrips(wiki_db):
    """An appended edition stores its full body + provenance, keyed by
    (entity_id, version), readable back verbatim."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    insert_page_version(
        wiki_db,
        entity_id="e_rag",
        version=1,
        content_hash="abc123",
        summary="RAG grounds an LLM in retrieved docs.",
        num_sources=2,
        source_id="content_1",
        source_type="raw_store",
        content="# RAG\n\nv1 body",
        created_at=NOW,
    )
    wiki_db.commit()
    row = wiki_db.execute(
        "SELECT version, content_hash, summary, num_sources, source_id, "
        "source_type, content, created_at FROM page_versions WHERE entity_id = ?",
        ("e_rag",),
    ).fetchone()
    assert tuple(row) == (
        1,
        "abc123",
        "RAG grounds an LLM in retrieved docs.",
        2,
        "content_1",
        "raw_store",
        "# RAG\n\nv1 body",
        NOW,
    )


def _add_version(conn, entity_id, version, *, content, summary="s", source_id="content_1"):
    insert_page_version(
        conn,
        entity_id=entity_id,
        version=version,
        content_hash=f"h{version}",
        summary=summary,
        num_sources=version,
        source_id=source_id,
        source_type="raw_store",
        content=content,
        created_at=f"2026-06-2{version}T00:00:00+00:00",
    )


def test_get_page_history_newest_first_metadata(wiki_db):
    """History is the edition index — metadata newest-first, no full bodies."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _add_version(wiki_db, "e_rag", 1, content="v1 body", source_id="content_1")
    _add_version(wiki_db, "e_rag", 2, content="v2 body", source_id="content_2")
    wiki_db.commit()

    history = get_page_history(wiki_db, "e_rag")
    assert [h.version for h in history] == [2, 1]
    assert history[0].source_id == "content_2"
    assert history[0].num_sources == 2


def test_get_page_version_returns_body_or_none(wiki_db):
    """get_page_version fetches one edition's full body; None for a version that
    was never recorded."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _add_version(wiki_db, "e_rag", 1, content="# RAG\n\nv1 body")
    _add_version(wiki_db, "e_rag", 2, content="# RAG\n\nv2 body, revised")
    wiki_db.commit()

    assert get_page_version(wiki_db, "e_rag", 1) == "# RAG\n\nv1 body"
    assert get_page_version(wiki_db, "e_rag", 2) == "# RAG\n\nv2 body, revised"
    assert get_page_version(wiki_db, "e_rag", 3) is None


def test_get_page_history_empty_for_unknown_entity(wiki_db):
    assert get_page_history(wiki_db, "e_missing") == []


def test_page_version_reads_are_scoped_to_entity(wiki_db):
    """History and body reads must filter by entity_id — one entity's editions
    never leak into another's."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_llm", "LLM")
    _add_version(wiki_db, "e_rag", 1, content="rag body")
    _add_version(wiki_db, "e_llm", 1, content="llm body")
    wiki_db.commit()

    assert [h.entity_id for h in get_page_history(wiki_db, "e_rag")] == ["e_rag"]
    assert get_page_version(wiki_db, "e_rag", 1) == "rag body"
    assert get_page_version(wiki_db, "e_llm", 1) == "llm body"


def test_get_page_version_distinguishes_empty_body_from_missing(wiki_db):
    """A stored empty body returns '' (not None); None means no such version."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _add_version(wiki_db, "e_rag", 1, content="")
    wiki_db.commit()

    assert get_page_version(wiki_db, "e_rag", 1) == ""
    assert get_page_version(wiki_db, "e_rag", 2) is None


def test_page_version_requires_provenance(wiki_db):
    """An edition with no source_id can't answer "what changed it" — the schema
    rejects it (NOT NULL provenance is the edition-history contract)."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    with pytest.raises(sqlite3.IntegrityError):
        insert_page_version(
            wiki_db,
            entity_id="e_rag",
            version=1,
            content_hash="abc123",
            summary="s",
            num_sources=1,
            source_id=None,  # type: ignore[arg-type]
            source_type="raw_store",
            content="b",
            created_at=NOW,
        )


# --- processed_items ---


def test_insert_processed_inserts_new_row(wiki_db):
    insert_processed(wiki_db, item_id="content_abc", source_type="raw_store", status="ok")
    wiki_db.commit()
    assert get_processed_ids(wiki_db, status="ok") == {"content_abc"}


def test_insert_processed_upserts_on_conflict(wiki_db):
    insert_processed(
        wiki_db, item_id="content_abc", source_type="raw_store", status="error", error="boom"
    )
    insert_processed(wiki_db, item_id="content_abc", source_type="raw_store", status="ok")
    wiki_db.commit()
    assert get_processed_ids(wiki_db, status="ok") == {"content_abc"}
    assert get_processed_ids(wiki_db, status="error") == set()


def test_insert_processed_separate_source_types_coexist(wiki_db):
    """item_id alone is not unique; (item_id, source_type) is the PK."""
    insert_processed(wiki_db, item_id="abc", source_type="raw_store", status="ok")
    insert_processed(wiki_db, item_id="abc", source_type="local_file", status="ok")
    wiki_db.commit()
    rows = wiki_db.execute(
        "SELECT source_type FROM processed_items WHERE item_id = 'abc' ORDER BY source_type"
    ).fetchall()
    assert [r[0] for r in rows] == ["local_file", "raw_store"]


def test_get_failed_returns_only_errors(wiki_db):
    insert_processed(wiki_db, item_id="a", source_type="raw_store", status="ok")
    insert_processed(wiki_db, item_id="b", source_type="raw_store", status="error", error="oops")
    wiki_db.commit()
    failed = get_failed(wiki_db)
    assert len(failed) == 1
    assert failed[0].item_id == "b"
    assert failed[0].error == "oops"


# --- pages ---


def test_upsert_page_inserts_then_updates(wiki_db):
    _seed_entity(wiki_db, "e_rag", "RAG")
    upsert_page(wiki_db, entity_id="e_rag", file_path="rag-aaaa1111.md", related_ids=[])
    wiki_db.commit()

    rec = get_page(wiki_db, "e_rag")
    assert rec is not None
    assert rec.file_path == "rag-aaaa1111.md"
    assert rec.canonical_name == "RAG"  # joined from entities
    assert rec.page_type == "concept"
    assert rec.related_ids == []

    upsert_page(wiki_db, entity_id="e_rag", file_path="rag-aaaa1111.md", related_ids=["e_llm"])
    wiki_db.commit()
    assert get_page(wiki_db, "e_rag").related_ids == ["e_llm"]


def test_get_all_pages_orders_by_entity_id_and_joins_identity(wiki_db):
    _seed_entity(wiki_db, "e_aaa", "RAG")
    _seed_entity(wiki_db, "e_bbb", "Chroma", page_type="tool")
    upsert_page(wiki_db, entity_id="e_bbb", file_path="chroma-bbbb2222.md", related_ids=[])
    upsert_page(wiki_db, entity_id="e_aaa", file_path="rag-aaaa1111.md", related_ids=[])
    wiki_db.commit()

    pages = get_all_pages(wiki_db)
    assert [p.entity_id for p in pages] == ["e_aaa", "e_bbb"]
    assert [p.page_type for p in pages] == ["concept", "tool"]
    assert [p.canonical_name for p in pages] == ["RAG", "Chroma"]


def test_pages_file_path_is_unique(wiki_db):
    """A slug+shortid collision surfaces as a UNIQUE violation, not a silent
    overwrite of another entity's page file."""
    _seed_entity(wiki_db, "e_a", "Alpha")
    _seed_entity(wiki_db, "e_b", "Beta")
    upsert_page(wiki_db, entity_id="e_a", file_path="collide.md", related_ids=[])
    with pytest.raises(sqlite3.IntegrityError):
        upsert_page(wiki_db, entity_id="e_b", file_path="collide.md", related_ids=[])


def test_page_without_entity_raises_fk(wiki_db):
    """pages.entity_id FKs to entities — a page for an unknown entity is refused."""
    with pytest.raises(sqlite3.IntegrityError):
        upsert_page(wiki_db, entity_id="e_ghost", file_path="ghost.md", related_ids=[])


# --- aliases ---


def test_insert_aliases_writes_display_and_normalizes_key(wiki_db):
    _seed_entity(wiki_db, "e_rag", "RAG")
    insert_aliases(
        wiki_db,
        [("Retrieval-Augmented Generation", "e_rag"), ("Retrieval Augmented", "e_rag")],
    )
    wiki_db.commit()
    assert set(get_aliases_for_entity(wiki_db, "e_rag")) == {
        "Retrieval-Augmented Generation",
        "Retrieval Augmented",
    }


def test_insert_aliases_skips_duplicate_normalized_key(wiki_db):
    """Two surface forms that normalize to the same key collapse to one row."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    insert_aliases(wiki_db, [("RAG", "e_rag")])
    insert_aliases(wiki_db, [("  rag ", "e_rag")])
    wiki_db.commit()
    assert wiki_db.execute("SELECT count(*) FROM aliases").fetchone()[0] == 1


def test_insert_aliases_collision_first_entity_wins(wiki_db):
    """If two entities claim the same normalized alias, the first writer wins."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_ra", "Rust Analyzer", page_type="tool")
    insert_aliases(wiki_db, [("RA", "e_rag")])
    insert_aliases(wiki_db, [("RA", "e_ra")])
    wiki_db.commit()
    row = wiki_db.execute("SELECT entity_id FROM aliases WHERE normalized_alias = 'ra'").fetchone()
    assert row[0] == "e_rag"


def test_insert_aliases_no_entries_is_noop(wiki_db):
    insert_aliases(wiki_db, [])
    wiki_db.commit()
    assert wiki_db.execute("SELECT count(*) FROM aliases").fetchone()[0] == 0


def test_get_aliases_for_entity_returns_sorted(wiki_db):
    _seed_entity(wiki_db, "e_rag", "RAG")
    insert_aliases(
        wiki_db,
        [("Retrieval-Augmented Generation", "e_rag"), ("Retrieval Augmented", "e_rag")],
    )
    wiki_db.commit()
    aliases = get_aliases_for_entity(wiki_db, "e_rag")
    assert aliases == sorted(aliases)


def test_get_aliases_for_entity_unknown_returns_empty(wiki_db):
    assert get_aliases_for_entity(wiki_db, "e_missing") == []


def test_snapshot_aliases_uses_entity_canonical(wiki_db):
    """canonical_name comes from entities; the aliases table holds only the
    extra display forms."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    insert_aliases(wiki_db, [("Retrieval-Augmented Generation", "e_rag")])
    wiki_db.commit()

    store = snapshot_aliases(wiki_db)
    assert "e_rag" in store.entries
    entry = store.entries["e_rag"]
    assert entry.canonical == "RAG"
    assert entry.aliases == ["Retrieval-Augmented Generation"]


def test_snapshot_aliases_includes_entity_with_no_aliases(wiki_db):
    _seed_entity(wiki_db, "e_rag", "RAG")
    wiki_db.commit()
    store = snapshot_aliases(wiki_db)
    assert store.entries["e_rag"].canonical == "RAG"
    assert store.entries["e_rag"].aliases == []


def test_snapshot_aliases_empty_returns_empty_store(wiki_db):
    assert snapshot_aliases(wiki_db).entries == {}


# --- page_sources ---


def test_count_sources_for_entity_counts_distinct_items(wiki_db):
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_other", "Other")
    insert_page_source(wiki_db, entity_id="e_rag", item_id="content_a", source_type="raw_store")
    insert_page_source(wiki_db, entity_id="e_rag", item_id="content_b", source_type="raw_store")
    # Re-inserting the same edge is idempotent — still one distinct contribution.
    insert_page_source(wiki_db, entity_id="e_rag", item_id="content_a", source_type="raw_store")
    # A different entity's edge must not bleed into this count.
    insert_page_source(wiki_db, entity_id="e_other", item_id="content_z", source_type="raw_store")
    wiki_db.commit()
    assert count_sources_for_entity(wiki_db, "e_rag") == 2


def test_count_sources_for_entity_unknown_returns_zero(wiki_db):
    assert count_sources_for_entity(wiki_db, "e_missing") == 0


def test_is_source_for_entity_reflects_ledger(wiki_db):
    _seed_entity(wiki_db, "e_rag", "RAG")
    insert_page_source(wiki_db, entity_id="e_rag", item_id="content_a", source_type="raw_store")
    wiki_db.commit()
    assert is_source_for_entity(wiki_db, "e_rag", "content_a") is True
    assert is_source_for_entity(wiki_db, "e_rag", "content_b") is False


def test_page_source_without_entity_raises_fk(wiki_db):
    with pytest.raises(sqlite3.IntegrityError):
        insert_page_source(wiki_db, entity_id="e_ghost", item_id="x", source_type="raw_store")
