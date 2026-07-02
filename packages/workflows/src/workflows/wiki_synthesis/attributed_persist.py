"""Attributed-lane persist — write one source's `SummaryAssignment` into wiki.db.

Bridges the pure assignment step (`entity_assignment.assign_summary`, which maps
each claim to the entity/entities it is about) and the claim-centric storage
layer (`domains.wiki.attributed`). Takes the per-source assignment plus the
source's attribution metadata and writes, in the caller's transaction: the
source row, any entities minted this run, each claim, and each claim→entity link.

Idempotent by construction — sources UPSERT on content_key, claims and
claim_entities carry deterministic ids and `ON CONFLICT DO NOTHING` unique keys —
so re-synthesising a source re-writes the same rows rather than duplicating them.
The caller opens the connection and brackets the transaction (one per source).
"""

from datetime import UTC, datetime
from sqlite3 import Connection

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    claim_text_hash,
    delete_claims_for_source,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    upsert_source,
)
from domains.wiki.state import insert_entity

from workflows.wiki_synthesis.entity_assignment import SummaryAssignment


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def persist_source_assignment(
    conn: Connection,
    *,
    assignment: SummaryAssignment,
    source: SourceRecord,
    synthesized_at: str | None = None,
) -> str:
    """Persist one source's attributed claims. Caller manages the transaction.

    Writes the source (UPSERT on content_key), the entities minted this run, then
    every claim and its claim→entity links. A claim assigned to no entity is
    still stored (it belongs to the source) — it simply links to nothing and
    renders on no page. Claim ids are minted deterministically from the SURVIVING
    source_id so a re-run's links reference the row `insert_claim` kept. Returns
    the surviving source_id.

    Claims are REPLACED, not merged: the source's existing claims are deleted
    before re-inserting, so a re-extraction's page reflects only the current claim
    set (append-only `UNIQUE(source_id, text_hash)` would otherwise keep stale
    ones). `synthesized_at` is the incremental-sweep watermark recorded on the
    source (the max `extracted_at` consumed for it).
    """
    source_id = upsert_source(conn, source, synthesized_at=synthesized_at)
    delete_claims_for_source(conn, source_id)
    for entity in assignment.new_entities:
        insert_entity(conn, entity)

    created_at = _now_iso()
    for ca in assignment.assignments:
        text_hash = claim_text_hash(ca.claim.text)
        # Link to the SURVIVING claim_id insert_claim returns (the existing row's
        # on a re-run), not the freshly-minted one — so the claim_entities FK
        # always references a row that exists.
        claim_id = insert_claim(
            conn,
            ClaimRecord(
                claim_id=mint_claim_id(source_id, text_hash),
                source_id=source_id,
                text=ca.claim.text,
                text_hash=text_hash,
                claim_kind="opinion" if ca.claim.speculative else "reported",
                created_at=created_at,
            ),
        )
        for entity_id in ca.entity_ids:
            insert_claim_entity(conn, claim_id=claim_id, entity_id=entity_id)

    return source_id
