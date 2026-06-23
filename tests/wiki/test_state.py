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
    MergeResult,
    RejectedRecord,
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
    get_rejected,
    get_related_for_entity,
    get_source_ids_for_entity,
    insert_aliases,
    insert_entity,
    insert_entity_relation,
    insert_page_source,
    insert_page_version,
    insert_processed,
    is_source_for_entity,
    merge_entities,
    snapshot_aliases,
    upsert_page,
    upsert_rejected,
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


def test_get_source_ids_for_entity_distinct_ordered(wiki_db):
    """The accumulated, distinct source item_ids for an entity, ordered by first
    contribution (MIN added_at) then item_id — the deterministic list the page
    frontmatter should render (vs the per-item [source_id] the LLM emits)."""
    _seed_entity(wiki_db, "e_rag", "RAG")
    _seed_entity(wiki_db, "e_other", "Other")
    # Direct inserts with controlled added_at to pin ordering deterministically.
    rows = [
        ("e_rag", "content_b", "raw_store", "2026-06-02T00:00:00+00:00"),
        ("e_rag", "content_a", "raw_store", "2026-06-01T00:00:00+00:00"),
        # Same item under a second source_type — one distinct item, earliest wins.
        ("e_rag", "content_a", "wiki", "2026-05-30T00:00:00+00:00"),
        ("e_other", "content_z", "raw_store", "2026-06-01T00:00:00+00:00"),
    ]
    wiki_db.executemany(
        "INSERT INTO page_sources (entity_id, item_id, source_type, added_at) VALUES (?,?,?,?)",
        rows,
    )
    wiki_db.commit()
    # content_a first (MIN added_at 2026-05-30 via the wiki row), then content_b.
    assert get_source_ids_for_entity(wiki_db, "e_rag") == ["content_a", "content_b"]
    assert get_source_ids_for_entity(wiki_db, "e_missing") == []


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


# --------------------------------------------------------------------------
# merge_entities — the destructive dedup primitive (#15)
# --------------------------------------------------------------------------


def test_merge_entities_repoints_page_sources(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    insert_page_source(wiki_db, entity_id="e_drop", item_id="art1", source_type="raw_store")
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert get_source_ids_for_entity(wiki_db, "e_keep") == ["art1"]
    assert get_entity(wiki_db, "e_drop") is None


def test_merge_entities_dedupes_shared_page_source(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    insert_page_source(wiki_db, entity_id="e_keep", item_id="art1", source_type="raw_store")
    insert_page_source(wiki_db, entity_id="e_drop", item_id="art1", source_type="raw_store")
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert count_sources_for_entity(wiki_db, "e_keep") == 1


def test_merge_entities_repoints_entity_relations_both_columns(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    _seed_entity(wiki_db, "e_x", "Anthropic")
    insert_entity_relation(
        wiki_db,
        entity_id="e_drop",
        related_entity_id="e_x",
        item_id="art1",
        source_type="raw_store",
    )
    insert_entity_relation(
        wiki_db,
        entity_id="e_x",
        related_entity_id="e_drop",
        item_id="art1",
        source_type="raw_store",
    )
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert get_related_for_entity(wiki_db, "e_keep") == ["e_x"]
    assert get_related_for_entity(wiki_db, "e_x") == ["e_keep"]


def test_merge_entities_deletes_the_drop_keep_self_edge(wiki_db):
    """A drop↔keep co-occurrence becomes a keep↔keep self-edge on re-point and
    must be removed; keep's real edges to other entities survive."""
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    _seed_entity(wiki_db, "e_x", "Anthropic")
    for a, b in [("e_drop", "e_keep"), ("e_keep", "e_drop"), ("e_drop", "e_x"), ("e_x", "e_drop")]:
        insert_entity_relation(
            wiki_db, entity_id=a, related_entity_id=b, item_id="art1", source_type="raw_store"
        )
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert get_related_for_entity(wiki_db, "e_keep") == ["e_x"]


def test_merge_entities_repoints_existing_aliases(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    insert_aliases(wiki_db, [("mp", "e_drop")])
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert build_entity_index(wiki_db).by_normalized_alias["mp"] == "e_keep"


def test_merge_entities_aliases_drop_name_onto_keep(wiki_db):
    """The load-bearing line: drop's name becomes an alias of keep so the next
    article saying 'Max plan' folds in instead of re-minting the dup."""
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=True)

    assert build_entity_index(wiki_db).by_normalized_alias["max plan"] == "e_keep"


def test_merge_entities_fails_when_drop_name_claimed_by_third_entity(wiki_db):
    """Codex CONCERN 1: a silent ON CONFLICT skip would leave the alias unwritten
    and re-mint the dup. Instead fail loudly when a different sense owns the name."""
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    _seed_entity(wiki_db, "e_other", "Mobile data plan")
    insert_aliases(wiki_db, [("Max plan", "e_other")])
    wiki_db.commit()

    with pytest.raises(ValueError, match="max plan"):
        with wiki_db:
            merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=True)

    # rolled back — drop survives, nothing folded into keep
    assert get_entity(wiki_db, "e_drop") is not None
    assert build_entity_index(wiki_db).by_normalized_alias["max plan"] == "e_other"


def test_merge_entities_no_alias_leaves_drop_name_unclaimed(wiki_db):
    """Homonym escape hatch: --no-alias keeps drop's name out of the alias table
    so a future different-sense mention mints fresh (safe false-split)."""
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert "max plan" not in build_entity_index(wiki_db).by_normalized_alias


def test_merge_entities_no_alias_drops_a_preexisting_self_alias(wiki_db):
    """--no-alias must keep drop's NAME from resolving to keep even when drop
    already had a self-alias row — re-pointing it would silently defeat the
    homonym guard (the sole prevention)."""
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    insert_aliases(wiki_db, [("Max plan", "e_drop")])  # self-alias: norm == drop's name
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert "max plan" not in build_entity_index(wiki_db).by_normalized_alias


def test_merge_entities_removes_drop_page_and_returns_its_path(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    _seed_entity(wiki_db, "e_drop", "Max plan")
    upsert_page(wiki_db, entity_id="e_drop", file_path="max-plan-34db8db7.md", related_ids=[])
    wiki_db.commit()

    with wiki_db:
        result = merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert result == MergeResult(
        keep_id="e_keep", drop_id="e_drop", drop_file_path="max-plan-34db8db7.md"
    )
    assert get_page(wiki_db, "e_drop") is None


def test_merge_entities_rejects_self_merge(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    wiki_db.commit()

    with pytest.raises(ValueError, match="itself"):
        with wiki_db:
            merge_entities(wiki_db, keep_id="e_keep", drop_id="e_keep")

    assert get_entity(wiki_db, "e_keep") is not None


def test_merge_entities_rejects_missing_participant(wiki_db):
    _seed_entity(wiki_db, "e_keep", "Claude Max")
    wiki_db.commit()

    with pytest.raises(ValueError, match="e_ghost"):
        with wiki_db:
            merge_entities(wiki_db, keep_id="e_keep", drop_id="e_ghost")

    with pytest.raises(ValueError, match="e_ghost"):
        with wiki_db:
            merge_entities(wiki_db, keep_id="e_ghost", drop_id="e_keep")

    assert get_entity(wiki_db, "e_keep") is not None
