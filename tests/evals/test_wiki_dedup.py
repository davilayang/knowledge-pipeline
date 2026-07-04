"""Tests for the `wiki-dedup-candidates` CLI orchestration (evals.wiki_dedup).

`run_candidates` reads entities + claims from a wiki.db, embeds (injected fake
here), and returns near-duplicate pairs. The OpenAI wiring is only reached via
`main`, so these stay offline.
"""

import json

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    claim_text_hash,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import connection, insert_entity
from evals.wiki_dedup import pairs_to_json, run_candidates

NOW = "2026-07-04T00:00:00+00:00"


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0, 1.0] if "Kubernetes" in t else [1.0, 0.0] for t in texts]


def _seed(conn, entity_id, canonical, claim_text):
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
    src = upsert_source(
        conn,
        SourceRecord(
            source_id=f"src_{entity_id}",
            content_key=f"medium::https://example.com/{entity_id}",
            origin_type="queue",
            title="t",
            author="a",
            publication="p",
            url=f"https://example.com/{entity_id}",
            published_at="2026-03-01",
            content_hash="h",
            fetched_at=NOW,
            added_at=NOW,
        ),
    )
    th = claim_text_hash(claim_text)
    cid = insert_claim(
        conn,
        ClaimRecord(
            claim_id=mint_claim_id(src, th),
            source_id=src,
            text=claim_text,
            text_hash=th,
            claim_kind="reported",
            created_at=NOW,
        ),
    )
    insert_claim_entity(conn, claim_id=cid, entity_id=entity_id)


def test_run_candidates_reads_db_and_surfaces_pairs(tmp_path, wiki_db_path):
    with connection(wiki_db_path) as conn, conn:
        _seed(conn, "e_a", "Claude Max", "Max is Anthropic's top tier.")
        _seed(conn, "e_b", "Max plan", "Max is Anthropic's paid plan.")
        _seed(conn, "e_c", "Kubernetes", "Kubernetes orchestrates containers.")

    pairs = run_candidates(wiki_db_path, _fake_embed, threshold=0.8)

    assert len(pairs) == 1
    assert {pairs[0].a.entity_id, pairs[0].b.entity_id} == {"e_a", "e_b"}

    # JSON is emit-able for a judging session: both sides + score.
    payload = json.loads(pairs_to_json(pairs))
    assert payload[0]["a"]["name"] in {"Claude Max", "Max plan"}
    assert payload[0]["score"] >= 0.8
