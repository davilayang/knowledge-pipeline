"""Tests for domains.wiki.attributed — the claim-centric wiki.db tables
(sources / claims / claim_entities) and the attributed-page render query.

Uses the `wiki_db` fixture (a fresh SQLite wiki.db with the schema applied).
"""

from domains.wiki.attributed import (
    AttributedClaim,
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    count_sources_for_entity,
    get_claims_for_source,
    get_source,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    mint_source_id,
    render_attributed_markdown,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import insert_entity

NOW = "2026-07-02T00:00:00+00:00"


def _entity_record(entity_id, canonical, *, entity_type="concept") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        entity_type=entity_type,
        created_at=NOW,
    )


def _seed_entity(conn, entity_id, canonical, *, entity_type="concept") -> EntityRecord:
    ent = _entity_record(entity_id, canonical, entity_type=entity_type)
    insert_entity(conn, ent)
    return ent


def _source(**over) -> SourceRecord:
    base = dict(
        source_id="src_0000000000000001",
        content_key="medium::https://example.com/a",
        origin_type="queue",
        title="A Title",
        author="Jane Doe",
        publication="Codrift",
        url="https://example.com/a",
        published_at="2026-03-01",
        content_hash="hash1",
        fetched_at=NOW,
        added_at=NOW,
    )
    base.update(over)
    return SourceRecord(**base)


# --- sources ---


def test_upsert_source_then_get_roundtrips(wiki_db):
    src = _source()
    upsert_source(wiki_db, src)
    wiki_db.commit()
    assert get_source(wiki_db, "src_0000000000000001") == src


def test_upsert_source_same_content_key_updates_in_place(wiki_db):
    # A re-fetch of the same article (same content_key, freshly-minted source_id)
    # updates the existing row's metadata and keeps the ORIGINAL source_id — so
    # claims already FK'd to it aren't orphaned. Returns the surviving id.
    upsert_source(wiki_db, _source(source_id="src_0000000000000001", title="Old"))
    kept = upsert_source(
        wiki_db,
        _source(source_id="src_9999999999999999", title="New", author="Updated Author"),
    )
    wiki_db.commit()

    assert kept == "src_0000000000000001"
    assert get_source(wiki_db, "src_9999999999999999") is None
    survivor = get_source(wiki_db, "src_0000000000000001")
    assert survivor.title == "New"
    assert survivor.author == "Updated Author"


def test_upsert_source_null_field_keeps_prior_value(wiki_db):
    # A degraded re-fetch (same content_key, some metadata now NULL) must NOT
    # clobber last-known-good attribution — COALESCE keeps the prior value.
    upsert_source(wiki_db, _source(title="Good Title", author="Real Author"))
    upsert_source(wiki_db, _source(source_id="src_degraded", title=None, author=None))
    wiki_db.commit()

    survivor = get_source(wiki_db, "src_0000000000000001")
    assert survivor.title == "Good Title"
    assert survivor.author == "Real Author"


# --- claims ---


def _claim(**over) -> ClaimRecord:
    text = over.pop("text", "Claude Code shipped subagents.")
    base = dict(
        claim_id="clm_0000000000000001",
        source_id="src_0000000000000001",
        text=text,
        text_hash=claim_text_hash(text),
        claim_kind="reported",
        created_at=NOW,
    )
    base.update(over)
    return ClaimRecord(**base)


def test_insert_claim_then_get_for_source_roundtrips(wiki_db):
    upsert_source(wiki_db, _source())
    clm = _claim()
    insert_claim(wiki_db, clm)
    wiki_db.commit()
    assert get_claims_for_source(wiki_db, "src_0000000000000001") == [clm]


def test_insert_claim_idempotent_on_source_and_text_hash(wiki_db):
    # Re-extracting a source re-inserts the same claim text (same text_hash) under
    # a new claim_id; the (source_id, text_hash) key collapses it to one row and
    # the first writer's id survives — so a re-run doesn't duplicate claims.
    upsert_source(wiki_db, _source())
    insert_claim(wiki_db, _claim(claim_id="clm_first"))
    insert_claim(wiki_db, _claim(claim_id="clm_second"))
    wiki_db.commit()
    claims = get_claims_for_source(wiki_db, "src_0000000000000001")
    assert [c.claim_id for c in claims] == ["clm_first"]


def test_insert_claim_returns_surviving_claim_id(wiki_db):
    # insert_claim returns the id of the row that actually exists (the first
    # writer's, on conflict) — like upsert_source. The persist path links
    # claim_entities to THIS return, so a pre-existing row can't leave the link
    # pointing at a non-existent id (FK break).
    upsert_source(wiki_db, _source())
    assert insert_claim(wiki_db, _claim(claim_id="clm_first")) == "clm_first"
    assert insert_claim(wiki_db, _claim(claim_id="clm_second")) == "clm_first"


# --- claim_entities + attributed-page render ---


def test_attributed_claims_for_entity_dated_first_undated_last(wiki_db):
    # A page renders from the claims attributed to its entity, each carrying its
    # source's attribution (publication / published_at / url). Dated sources sort
    # ascending; undated (NULL published_at) float to the END, not the top.
    _seed_entity(wiki_db, "e_x", "GraphRAG")
    upsert_source(
        wiki_db,
        _source(
            source_id="src_dated",
            content_key="k1",
            publication="Codrift",
            url="https://ex.com/d",
            published_at="2026-03-01",
        ),
    )
    upsert_source(
        wiki_db,
        _source(
            source_id="src_undated",
            content_key="k2",
            publication="Voidmag",
            url="https://ex.com/u",
            published_at=None,
        ),
    )
    insert_claim(
        wiki_db,
        _claim(
            claim_id="clm_undated", source_id="src_undated", text="Undated claim about GraphRAG."
        ),
    )
    insert_claim(
        wiki_db,
        _claim(
            claim_id="clm_dated",
            source_id="src_dated",
            text="Dated claim about GraphRAG.",
            claim_kind="opinion",
        ),
    )
    insert_claim_entity(wiki_db, claim_id="clm_undated", entity_id="e_x")
    insert_claim_entity(wiki_db, claim_id="clm_dated", entity_id="e_x")
    wiki_db.commit()

    assert attributed_claims_for_entity(wiki_db, "e_x") == [
        AttributedClaim(
            text="Dated claim about GraphRAG.",
            claim_kind="opinion",
            author="Jane Doe",
            publication="Codrift",
            published_at="2026-03-01",
            url="https://ex.com/d",
        ),
        AttributedClaim(
            text="Undated claim about GraphRAG.",
            claim_kind="reported",
            author="Jane Doe",
            publication="Voidmag",
            published_at=None,
            url="https://ex.com/u",
        ),
    ]


# --- deterministic id minting (idempotent write path) ---


def test_mint_source_id_is_deterministic_over_content_key():
    key = "medium::https://example.com/a"
    minted = mint_source_id(key)
    assert minted == mint_source_id(key)
    assert minted.startswith("src_")
    assert mint_source_id("medium::https://example.com/b") != minted


def test_mint_claim_id_is_deterministic_and_scoped_to_source():
    # Same claim text in two different sources → distinct claim ids (the id is a
    # global PK), but stable across re-runs of the SAME (source, text) so a re-run
    # references the row insert_claim kept rather than a fresh, unlinked id.
    text_hash = claim_text_hash("Some claim.")
    a = mint_claim_id("src_aaa", text_hash)
    assert a == mint_claim_id("src_aaa", text_hash)
    assert a.startswith("clm_")
    assert mint_claim_id("src_bbb", text_hash) != a


# --- derived num_sources ---


def test_count_sources_for_entity_counts_distinct_sources(wiki_db):
    # num_sources(E) = COUNT(DISTINCT source) over E's claims — two claims from
    # one source and one from another count as 2, not 3.
    _seed_entity(wiki_db, "e_x", "GraphRAG")
    upsert_source(wiki_db, _source(source_id="src_a", content_key="ka"))
    upsert_source(wiki_db, _source(source_id="src_b", content_key="kb"))
    insert_claim(wiki_db, _claim(claim_id="c1", source_id="src_a", text="one"))
    insert_claim(wiki_db, _claim(claim_id="c2", source_id="src_a", text="two"))
    insert_claim(wiki_db, _claim(claim_id="c3", source_id="src_b", text="three"))
    for cid in ("c1", "c2", "c3"):
        insert_claim_entity(wiki_db, claim_id=cid, entity_id="e_x")
    wiki_db.commit()

    assert count_sources_for_entity(wiki_db, "e_x") == 2


def test_count_sources_for_entity_unknown_is_zero(wiki_db):
    assert count_sources_for_entity(wiki_db, "e_missing") == 0


# --- attributed page render (markdown) ---


def test_render_attributed_markdown_shape():
    # The page renders claims split into ## Reported / ## Opinion sections (the
    # header conveys the kind, so the inline tag is dropped), each attributed by
    # author · domain (date) — the memory hooks (specifics over abstractions).
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="GraphRAG combines vector search with a knowledge graph.",
            claim_kind="reported",
            author="Jane Doe",
            publication=None,
            published_at="2026-03-01",
            url="https://medium.com/x",
        ),
        AttributedClaim(
            text="GraphRAG will replace naive RAG.",
            claim_kind="opinion",
            author=None,
            publication=None,
            published_at=None,
            url="https://voidmag.com/y",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity,
        claims=claims,
        aliases=["Graph RAG"],
        num_sources=2,
        updated_at="2026-07-02",
    )
    assert md == (
        "---\n"
        "entity_id: e_x\n"
        "title: GraphRAG\n"
        "entity_type: concept\n"
        "aliases: [Graph RAG]\n"
        "num_sources: 2\n"
        "updated_at: 2026-07-02\n"
        "---\n"
        "\n"
        "# GraphRAG\n"
        "\n"
        "## Reported\n"
        "\n"
        "- GraphRAG combines vector search with a knowledge graph. "
        "— Jane Doe · medium.com (2026-03-01)\n"
        "\n"
        "## Opinion\n"
        "\n"
        "- GraphRAG will replace naive RAG. — voidmag.com\n"
    )


def test_render_attributed_markdown_omits_empty_section():
    # An entity with only reported claims renders just the ## Reported section —
    # no empty ## Opinion header.
    entity = _entity_record("e_y", "RAG")
    claims = [
        AttributedClaim(
            text="RAG retrieves before generating.",
            claim_kind="reported",
            author="A",
            publication=None,
            published_at="2026-01-01",
            url="https://medium.com/z",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-02"
    )
    assert "## Reported" in md
    assert "## Opinion" not in md
