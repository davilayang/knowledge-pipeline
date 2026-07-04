"""Tests for domains.wiki.dedup — the wiki.db reader for the dedup loop (#15).

`load_entity_texts` reads each entity's name + top-N claim texts from wiki.db (the
claim-centric embed source — there is no per-entity summary column). The numeric
pairwise-cosine search (`find_merge_candidates`) lives in `evals.wiki_dedup` (it
needs numpy) and is tested there. Uses the `wiki_db` fixture.
"""

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    claim_text_hash,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    upsert_source,
)
from domains.wiki.dedup import load_entity_texts
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import insert_entity

NOW = "2026-07-04T00:00:00+00:00"


def _seed_entity(conn, entity_id, canonical):
    insert_entity(
        conn,
        EntityRecord(
            entity_id=entity_id,
            canonical_name=canonical,
            normalized_name=normalize_name(canonical),
            slug=slugify(canonical),
            entity_type="concept",
            created_at=NOW,
        ),
    )


def _seed_claim(conn, source_id, entity_id, text):
    th = claim_text_hash(text)
    cid = insert_claim(
        conn,
        ClaimRecord(
            claim_id=mint_claim_id(source_id, th),
            source_id=source_id,
            text=text,
            text_hash=th,
            claim_kind="reported",
            created_at=NOW,
        ),
    )
    insert_claim_entity(conn, claim_id=cid, entity_id=entity_id)


def test_load_entity_texts_embeds_name_plus_top_claims(wiki_db):
    _seed_entity(wiki_db, "e_a", "Claude Max")
    src = upsert_source(
        wiki_db,
        SourceRecord(
            source_id="src_0000000000000001",
            content_key="medium::https://example.com/a",
            origin_type="queue",
            title="t",
            author="a",
            publication="p",
            url="https://example.com/a",
            published_at="2026-03-01",
            content_hash="h",
            fetched_at=NOW,
            added_at=NOW,
        ),
    )
    _seed_claim(wiki_db, src, "e_a", "Max is Anthropic's top tier.")
    _seed_claim(wiki_db, src, "e_a", "Max costs more than Pro.")
    _seed_claim(wiki_db, src, "e_a", "Max raises rate limits.")
    wiki_db.commit()

    items = load_entity_texts(wiki_db, top_n=2)

    assert len(items) == 1
    (item,) = items
    assert item.canonical_name == "Claude Max"
    assert "Max is Anthropic's top tier." in item.text
    assert "Max costs more than Pro." in item.text
    assert "Max raises rate limits." not in item.text  # capped at top_n=2
