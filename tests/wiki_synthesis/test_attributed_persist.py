"""Tests for the attributed-lane persist orchestration — turning a per-source
`SummaryAssignment` (claims mapped to entities) into rows in wiki.db's
claim-centric tables (sources / claims / claim_entities) in one transaction.

Uses the `wiki_db` fixture (a fresh SQLite wiki.db with the schema applied).
"""

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    get_source,
    get_synthesized_watermarks,
    insert_claim,
    mint_source_id,
    upsert_source,
)
from domains.wiki.claims import SourceClaim
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import get_related_for_entity
from workflows.wiki_synthesis.attributed_persist import persist_source_assignment
from workflows.wiki_synthesis.entity_assignment import ClaimAssignment, SummaryAssignment


def _single_claim_assignment(item_id, text) -> SummaryAssignment:
    ex = _entity("e_x", "GraphRAG")
    c = SourceClaim(text=text, source_id=item_id)
    return SummaryAssignment(
        item_id=item_id,
        assignments=(ClaimAssignment(claim=c, entity_ids=("e_x",)),),
        entities={"e_x": ex},
        new_entities=(ex,),
    )


NOW = "2026-07-02T00:00:00+00:00"


def _entity(entity_id, canonical) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        entity_type="concept",
        created_at=NOW,
    )


def _source(content_key) -> SourceRecord:
    return SourceRecord(
        source_id=mint_source_id(content_key),
        content_key=content_key,
        origin_type="queue",
        title="A Title",
        author=None,
        publication="Codrift",
        url="https://ex.com/a",
        published_at="2026-03-01",
        content_hash="h1",
        fetched_at=NOW,
        added_at=NOW,
    )


def _assignment(item_id) -> SummaryAssignment:
    ex, ey = _entity("e_x", "GraphRAG"), _entity("e_y", "Microsoft")
    c1 = SourceClaim(text="GraphRAG uses a knowledge graph.", source_id=item_id)
    c2 = SourceClaim(text="Microsoft ships GraphRAG.", source_id=item_id, speculative=True)
    return SummaryAssignment(
        item_id=item_id,
        assignments=(
            ClaimAssignment(claim=c1, entity_ids=("e_x",)),
            ClaimAssignment(claim=c2, entity_ids=("e_y", "e_x")),
        ),
        entities={"e_x": ex, "e_y": ey},
        new_entities=(ex, ey),
    )


def test_persist_writes_source_claims_and_entity_links(wiki_db):
    persist_source_assignment(
        wiki_db, assignment=_assignment("medium::u"), source=_source("medium::u")
    )
    wiki_db.commit()

    src_id = mint_source_id("medium::u")
    assert get_source(wiki_db, src_id).publication == "Codrift"

    x_texts = {c.text for c in attributed_claims_for_entity(wiki_db, "e_x")}
    assert x_texts == {"GraphRAG uses a knowledge graph.", "Microsoft ships GraphRAG."}

    y_claims = attributed_claims_for_entity(wiki_db, "e_y")
    assert [(c.text, c.stance) for c in y_claims] == [("Microsoft ships GraphRAG.", "opinion")]


def test_persist_is_idempotent_across_reruns(wiki_db):
    # Re-synthesising the same source (deterministic source/claim ids + ON
    # CONFLICT keys) re-writes the same rows, never duplicates them.
    for _ in range(2):
        persist_source_assignment(
            wiki_db, assignment=_assignment("medium::u"), source=_source("medium::u")
        )
        wiki_db.commit()

    assert len(attributed_claims_for_entity(wiki_db, "e_x")) == 2
    assert len(attributed_claims_for_entity(wiki_db, "e_y")) == 1


def test_persist_replaces_claims_on_reprocess(wiki_db):
    # A re-extracted source's claims REPLACE the prior set — append-only claims
    # (UNIQUE(source_id, text_hash)) would otherwise accumulate stale ones. Persist
    # deletes the source's existing claims before re-inserting the new set.
    persist_source_assignment(
        wiki_db, assignment=_assignment("medium::u"), source=_source("medium::u")
    )
    wiki_db.commit()

    persist_source_assignment(
        wiki_db,
        assignment=_single_claim_assignment("medium::u", "A totally new claim about GraphRAG."),
        source=_source("medium::u"),
    )
    wiki_db.commit()

    x_texts = {c.text for c in attributed_claims_for_entity(wiki_db, "e_x")}
    assert x_texts == {"A totally new claim about GraphRAG."}


def _co_assignment(item_id, other_id, other_name) -> SummaryAssignment:
    # One claim co-mentioning e_x and `other_id` → both link to this source, so they
    # co-occur. Reused to re-persist the SAME source with a different second entity.
    ex = _entity("e_x", "GraphRAG")
    other = _entity(other_id, other_name)
    c = SourceClaim(text=f"GraphRAG relates to {other_name}.", source_id=item_id)
    return SummaryAssignment(
        item_id=item_id,
        assignments=(ClaimAssignment(claim=c, entity_ids=("e_x", other_id)),),
        entities={"e_x": ex, other_id: other},
        new_entities=(ex, other),
    )


def test_reextraction_updates_related_without_edge_upkeep(wiki_db):
    # Co-occurrence is DERIVED from claim_entities, not stored: re-persisting a
    # source with a changed entity set updates `related` on the next read, with no
    # edge-maintenance code — claim replacement drops the stale link, the derived
    # query reflects it. This is the whole argument for not materialising edges.
    persist_source_assignment(
        wiki_db,
        assignment=_co_assignment("medium::u", "e_y", "Microsoft"),
        source=_source("medium::u"),
    )
    wiki_db.commit()
    assert get_related_for_entity(wiki_db, "e_x") == ["e_y"]

    # Re-extract the SAME source, now co-mentioning e_z instead of e_y.
    persist_source_assignment(
        wiki_db, assignment=_co_assignment("medium::u", "e_z", "Neo4j"), source=_source("medium::u")
    )
    wiki_db.commit()
    assert get_related_for_entity(wiki_db, "e_x") == ["e_z"]
    assert get_related_for_entity(wiki_db, "e_y") == []  # stale co-occurrence gone


def test_persist_writes_synthesized_at_watermark(wiki_db):
    # The sweep passes the max(extracted_at) it consumed; persist records it on the
    # source so the next sweep can skip an unchanged source.
    persist_source_assignment(
        wiki_db,
        assignment=_assignment("medium::u"),
        source=_source("medium::u"),
        synthesized_at="2026-07-02T09:00:00+00:00",
    )
    wiki_db.commit()
    assert get_synthesized_watermarks(wiki_db) == {"medium::u": "2026-07-02T09:00:00+00:00"}


def test_persist_links_to_preexisting_claim_row(wiki_db):
    # A claim row for this (source, text) already exists under a NON-deterministic
    # id. persist must link claim_entities to THAT surviving row (via
    # insert_claim's return), not a freshly-minted id — else the FK breaks.
    src = _source("medium::u")
    text = "GraphRAG uses a knowledge graph."
    upsert_source(wiki_db, src)
    insert_claim(
        wiki_db,
        ClaimRecord(
            "clm_rogue", src.source_id, text, claim_text_hash(text), "source", "reported", NOW
        ),
    )
    wiki_db.commit()

    persist_source_assignment(wiki_db, assignment=_assignment("medium::u"), source=src)
    wiki_db.commit()

    assert text in {c.text for c in attributed_claims_for_entity(wiki_db, "e_x")}
