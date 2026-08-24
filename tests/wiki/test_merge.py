"""Tests for domains.wiki.state.merge_entities — the claim-centric entity-merge
transaction (#15). Folds a duplicate ("drop") into a survivor ("keep"): repoints
claim_entities + aliases, aliases drop's name onto keep, deletes drop, bumps
keep's page. Uses the `wiki_db` fixture (fresh SQLite wiki.db with schema).
"""

import pytest
from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import (
    get_aliases_for_entity,
    get_entity,
    get_page,
    insert_aliases,
    insert_entity,
    merge_entities,
    upsert_page,
)

NOW = "2026-07-04T00:00:00+00:00"
MERGE_NOW = "2026-12-31T00:00:00+00:00"


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


def _seed_source(conn, source_id="src_0000000000000001") -> str:
    return upsert_source(
        conn,
        SourceRecord(
            source_id=source_id,
            content_key=f"medium::https://example.com/{source_id}",
            origin_type="queue",
            title="A Title",
            author="Jane Doe",
            publication="Codrift",
            url="https://example.com/a",
            published_at="2026-03-01",
            content_hash="hash1",
            fetched_at=NOW,
            added_at=NOW,
        ),
    )


def _seed_claim(conn, source_id, text, *, provenance="source", stance="reported") -> str:
    th = claim_text_hash(text)
    return insert_claim(
        conn,
        ClaimRecord(
            claim_id=mint_claim_id(source_id, th),
            source_id=source_id,
            text=text,
            text_hash=th,
            provenance=provenance,
            stance=stance,
            created_at=NOW,
        ),
    )


def test_merge_folds_drop_into_keep(wiki_db):
    """Happy path: drop's claims move onto keep (a both-linked claim coalesces
    once), drop's name becomes an alias of keep, drop's entity+page are deleted,
    keep's page updated_at is bumped, and the result carries drop's file_path."""
    _seed_entity(wiki_db, "e_keep", "AI agents")
    _seed_entity(wiki_db, "e_drop", "AI agent")
    upsert_page(wiki_db, entity_id="e_keep", file_path="ai_agents-keep.md", related_ids=[])
    upsert_page(wiki_db, entity_id="e_drop", file_path="ai_agent-drop.md", related_ids=[])

    src = _seed_source(wiki_db)
    c1 = _seed_claim(wiki_db, src, "Agents call tools in a loop.")
    c2 = _seed_claim(wiki_db, src, "Agents need a harness.")
    c3 = _seed_claim(wiki_db, src, "Agents are non-deterministic.")
    # c1 is linked to BOTH keep and drop (the collision path); c2/c3 only drop.
    insert_claim_entity(wiki_db, claim_id=c1, entity_id="e_keep")
    insert_claim_entity(wiki_db, claim_id=c1, entity_id="e_drop")
    insert_claim_entity(wiki_db, claim_id=c2, entity_id="e_drop")
    insert_claim_entity(wiki_db, claim_id=c3, entity_id="e_drop")
    wiki_db.commit()

    with wiki_db:
        result = merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", now=MERGE_NOW)

    # keep now carries all three claims, the collision claim coalesced to once.
    keep_claims = attributed_claims_for_entity(wiki_db, "e_keep")
    assert len(keep_claims) == 3

    # drop's identity + page are gone; its name is now an alias of keep.
    assert get_entity(wiki_db, "e_drop") is None
    assert get_page(wiki_db, "e_drop") is None
    assert "AI agent" in get_aliases_for_entity(wiki_db, "e_keep")

    # result carries drop's file_path (caller unlinks it); keep's page is bumped.
    assert result.drop_file_path == "ai_agent-drop.md"
    assert get_page(wiki_db, "e_keep").updated_at == MERGE_NOW


def test_merge_raises_when_third_entity_owns_drops_name(wiki_db):
    """If a third entity already owns drop's normalized name as an alias, aliasing
    it onto keep would misroute future mentions — raise and mutate nothing."""
    _seed_entity(wiki_db, "e_keep", "AI agents")
    _seed_entity(wiki_db, "e_drop", "AI agent")
    _seed_entity(wiki_db, "e_third", "Agentic Interfaces")
    insert_aliases(wiki_db, [("AI agent", "e_third")])  # third owns "ai agent"
    src = _seed_source(wiki_db)
    c1 = _seed_claim(wiki_db, src, "Agents call tools.")
    insert_claim_entity(wiki_db, claim_id=c1, entity_id="e_drop")
    wiki_db.commit()

    with pytest.raises(ValueError, match="already"):
        with wiki_db:
            merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop")

    # nothing moved: drop still present with its claim, keep gained no alias.
    assert get_entity(wiki_db, "e_drop") is not None
    assert len(attributed_claims_for_entity(wiki_db, "e_drop")) == 1
    assert "AI agent" not in get_aliases_for_entity(wiki_db, "e_keep")


def test_merge_when_drop_carries_its_own_name_as_an_alias(wiki_db):
    """Regression: a drop that already has its own canonical name as an alias row
    must not PK-collide when that name is re-aliased onto keep. The self-alias
    must cascade-delete with drop, not repoint to keep ahead of the insert."""
    _seed_entity(wiki_db, "e_keep", "AI agents")
    _seed_entity(wiki_db, "e_drop", "AI agent")
    insert_aliases(wiki_db, [("AI agent", "e_drop")])  # drop's self-alias
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop")

    assert get_entity(wiki_db, "e_drop") is None
    assert "AI agent" in get_aliases_for_entity(wiki_db, "e_keep")


def test_merge_no_alias_skips_the_name_and_guards_self_merge(wiki_db):
    """alias=False is the homonym escape hatch: claims still fold, but drop's name
    is NOT aliased onto keep (so a future different-sense mention mints fresh).
    Merging an entity into itself is rejected."""
    _seed_entity(wiki_db, "e_keep", "Mercury")  # the planet
    _seed_entity(wiki_db, "e_drop", "Mercury (element)")
    src = _seed_source(wiki_db)
    c1 = _seed_claim(wiki_db, src, "Mercury is liquid at room temperature.")
    insert_claim_entity(wiki_db, claim_id=c1, entity_id="e_drop")
    wiki_db.commit()

    with wiki_db:
        merge_entities(wiki_db, keep_id="e_keep", drop_id="e_drop", alias=False)

    assert len(attributed_claims_for_entity(wiki_db, "e_keep")) == 1  # claim folded
    assert get_aliases_for_entity(wiki_db, "e_keep") == []  # name NOT aliased

    with pytest.raises(ValueError, match="itself"):
        with wiki_db:
            merge_entities(wiki_db, keep_id="e_keep", drop_id="e_keep")
