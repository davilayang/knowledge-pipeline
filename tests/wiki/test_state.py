"""Tests for domains.wiki.state — the SQLite (wiki.db) helpers.

Uses the `wiki_db` fixture from tests/conftest.py: a fresh sqlite3 connection to
a temp wiki.db with the schema applied.

Identity now lives in `entities`; pages/aliases/entity_relations FK to it, so every
test that writes one of those seeds its parent entity first (foreign_keys=ON).
"""

import sqlite3

import pytest
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import (
    RejectedRecord,
    build_entity_index,
    get_aliases_for_entity,
    get_all_pages,
    get_entity,
    get_failed,
    get_page,
    get_processed_ids,
    get_rejected,
    get_related_for_entity,
    insert_aliases,
    insert_entity,
    insert_entity_relation,
    insert_processed,
    reject_entity,
    snapshot_aliases,
    upsert_page,
    upsert_rejected,
)

NOW = "2026-06-22T00:00:00+00:00"


def _seed_entity(conn, entity_id, canonical, *, entity_type="concept") -> EntityRecord:
    ent = EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        entity_type=entity_type,
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
    _seed_entity(wiki_db, "e_rag", "RAG (changed)", entity_type="tool")
    wiki_db.commit()
    got = get_entity(wiki_db, "e_rag")
    assert got.canonical_name == "RAG"
    assert got.entity_type == "concept"


def test_build_entity_index_includes_names_and_aliases(wiki_db):
    _seed_entity(wiki_db, "e_mcp", "Model Context Protocol")
    insert_aliases(wiki_db, [("MCP", "e_mcp")])
    wiki_db.commit()

    index = build_entity_index(wiki_db)
    assert index.by_normalized_name["model context protocol"] == "e_mcp"
    assert index.by_normalized_alias["mcp"] == "e_mcp"


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
    assert rec.entity_type == "concept"
    assert rec.related_ids == []

    upsert_page(wiki_db, entity_id="e_rag", file_path="rag-aaaa1111.md", related_ids=["e_llm"])
    wiki_db.commit()
    assert get_page(wiki_db, "e_rag").related_ids == ["e_llm"]


def test_get_all_pages_orders_by_entity_id_and_joins_identity(wiki_db):
    _seed_entity(wiki_db, "e_aaa", "RAG")
    _seed_entity(wiki_db, "e_bbb", "Chroma", entity_type="tool")
    upsert_page(wiki_db, entity_id="e_bbb", file_path="chroma-bbbb2222.md", related_ids=[])
    upsert_page(wiki_db, entity_id="e_aaa", file_path="rag-aaaa1111.md", related_ids=[])
    wiki_db.commit()

    pages = get_all_pages(wiki_db)
    assert [p.entity_id for p in pages] == ["e_aaa", "e_bbb"]
    assert [p.entity_type for p in pages] == ["concept", "tool"]
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
    _seed_entity(wiki_db, "e_ra", "Rust Analyzer", entity_type="tool")
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


# --- entity_relations ---


def test_insert_entity_relation_roundtrips(wiki_db):
    """One ledger row = one directed edge (entity→related) tagged by the content
    item that produced the co-occurrence."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_llm", "LLM")
    insert_entity_relation(
        wiki_db,
        entity_id="e_rag",
        related_entity_id="e_llm",
        item_id="content_1",
        source_type="raw_store",
        added_at=NOW,
    )
    wiki_db.commit()
    row = wiki_db.execute(
        "SELECT entity_id, related_entity_id, item_id, source_type, added_at FROM entity_relations"
    ).fetchone()
    assert tuple(row) == ("e_rag", "e_llm", "content_1", "raw_store", NOW)


def test_insert_entity_relation_idempotent(wiki_db):
    """Re-inserting the same (edge, item) is a no-op — retry-safe."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_llm", "LLM")
    for _ in range(2):
        insert_entity_relation(
            wiki_db,
            entity_id="e_rag",
            related_entity_id="e_llm",
            item_id="content_1",
            source_type="raw_store",
            added_at=NOW,
        )
    wiki_db.commit()
    assert wiki_db.execute("SELECT count(*) FROM entity_relations").fetchone()[0] == 1


def _relate(conn, a, b, item, *, added_at):
    insert_entity_relation(
        conn,
        entity_id=a,
        related_entity_id=b,
        item_id=item,
        source_type="raw_store",
        added_at=added_at,
    )


def test_get_related_for_entity_ranks_by_derived_co_count(wiki_db):
    """Related ids ranked by co_count = COUNT(DISTINCT item_id) — how many
    distinct articles co-mention the pair. Re-seeing the same item doesn't
    inflate the count (derived, retry-safe)."""
    for eid, name in [("e_rag", "RAG"), ("e_llm", "LLM"), ("e_chroma", "Chroma")]:
        _seed_entity(wiki_db, eid, name)
    # e_llm co-occurs with e_rag in TWO articles; e_chroma in ONE.
    _relate(wiki_db, "e_rag", "e_llm", "content_1", added_at="2026-06-01T00:00:00+00:00")
    _relate(wiki_db, "e_rag", "e_llm", "content_2", added_at="2026-06-02T00:00:00+00:00")
    _relate(wiki_db, "e_rag", "e_llm", "content_2", added_at="2026-06-02T00:00:00+00:00")  # dup
    _relate(wiki_db, "e_rag", "e_chroma", "content_1", added_at="2026-06-01T00:00:00+00:00")
    wiki_db.commit()

    assert get_related_for_entity(wiki_db, "e_rag") == ["e_llm", "e_chroma"]
    assert get_related_for_entity(wiki_db, "e_rag", limit=1) == ["e_llm"]
    assert get_related_for_entity(wiki_db, "e_missing") == []


def test_get_related_for_entity_stable_tiebreak(wiki_db):
    """Equal co_count → newest last_seen first, then entity_id asc (stable)."""
    for eid, name in [("e_rag", "RAG"), ("e_a", "A"), ("e_b", "B")]:
        _seed_entity(wiki_db, eid, name)
    # Both co_count 1, but e_b seen later → e_b first.
    _relate(wiki_db, "e_rag", "e_a", "content_1", added_at="2026-06-01T00:00:00+00:00")
    _relate(wiki_db, "e_rag", "e_b", "content_2", added_at="2026-06-05T00:00:00+00:00")
    wiki_db.commit()
    assert get_related_for_entity(wiki_db, "e_rag") == ["e_b", "e_a"]


# --------------------------------------------------------------------------
# rejected_entities (curator denylist — durable, name-keyed)
# --------------------------------------------------------------------------


def test_upsert_and_get_rejected_round_trip(wiki_db):
    upsert_rejected(
        wiki_db,
        normalized_name="max plan",
        category="product",
        reason="dup of claude max",
        rejected_at=NOW,
    )
    wiki_db.commit()
    assert get_rejected(wiki_db) == [
        RejectedRecord(
            normalized_name="max plan",
            category="product",
            reason="dup of claude max",
            rejected_at=NOW,
        )
    ]


def test_insert_aliases_skips_rejected_names(wiki_db):
    """An alias must never contradict the denylist: a normalized alias already in
    rejected_entities is dropped, so a rejected surface form can't re-enter as an
    alias of a different entity (codex: alias reintroduction)."""
    _seed_entity(wiki_db, "e_x", "Privacy Stuff")
    upsert_rejected(wiki_db, normalized_name="cookie settings")
    insert_aliases(wiki_db, [("Cookie Settings", "e_x"), ("Privacy", "e_x")])
    wiki_db.commit()

    idx = build_entity_index(wiki_db)
    assert "cookie settings" not in idx.by_normalized_alias
    assert idx.by_normalized_alias["privacy"] == "e_x"


def test_upsert_rejected_updates_in_place_on_conflict(wiki_db):
    upsert_rejected(wiki_db, normalized_name="max plan", reason="first", rejected_at=NOW)
    later = "2026-06-23T12:00:00+00:00"
    upsert_rejected(wiki_db, normalized_name="max plan", reason="revised", rejected_at=later)
    wiki_db.commit()
    assert get_rejected(wiki_db) == [
        RejectedRecord(
            normalized_name="max plan",
            category=None,
            reason="revised",
            rejected_at=later,
        )
    ]


def test_reject_entity_denylists_name_and_deletes(wiki_db):
    _seed_entity(wiki_db, "e_junk", "Cookie Policy")
    wiki_db.commit()

    with wiki_db:
        reject_entity(wiki_db, entity_id="e_junk", category="chrome", reason="site boilerplate")

    assert get_entity(wiki_db, "e_junk") is None
    assert get_rejected(wiki_db) == [
        RejectedRecord(
            normalized_name="cookie policy",
            category="chrome",
            reason="site boilerplate",
            rejected_at=get_rejected(wiki_db)[0].rejected_at,
        )
    ]


def test_reject_entity_tombstones_the_whole_alias_family(wiki_db):
    """Rejecting denylists the canonical name AND every alias, so the deleted
    entity can't re-mint under a known surface form (the re-mint-loop guard)."""
    _seed_entity(wiki_db, "e_junk", "Cookie Policy")
    insert_aliases(wiki_db, [("Cookie Settings", "e_junk"), ("Cookie Notice", "e_junk")])
    wiki_db.commit()

    with wiki_db:
        reject_entity(wiki_db, entity_id="e_junk")

    assert {r.normalized_name for r in get_rejected(wiki_db)} == {
        "cookie policy",
        "cookie settings",
        "cookie notice",
    }


def test_reject_entity_rejects_missing_entity(wiki_db):
    with pytest.raises(ValueError, match="e_ghost"):
        with wiki_db:
            reject_entity(wiki_db, entity_id="e_ghost")
