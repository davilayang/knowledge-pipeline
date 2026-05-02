"""Two parallel writers race on the same alias; ON CONFLICT DO NOTHING
keeps exactly one row.

This is the SQL property that makes Dagster's dynamic-partitioned fan-out
(40 articles in parallel in PR 3) safe for alias writes. Without it, two
articles that both extract a new entity sharing an alias would either:
  - both insert and produce duplicate rows (UNIQUE constraint fires
    AFTER the duplicate is already on disk for the un-indexed case), OR
  - one transaction blocks the other indefinitely (lock contention).

The plan picked `INSERT ... ON CONFLICT (alias) DO NOTHING` precisely
because it gives lock-free first-writer-wins semantics. This test pins
that to the file.

Tests the SQL helper directly (not the full workflow under concurrency)
because the workflow path is dominated by LLM mocking — the property
under test is the SQL behavior. Production-load testing (full workflow
under contention) is deferred to Phase E.

Plan reference: ai-plannings/2026-05-02_workspace-phase-b-pr2.md → Property 5.
"""

import concurrent.futures
import threading

import psycopg
import pytest
from domains.wiki.state import insert_aliases_idempotent


@pytest.mark.timeout(15)
def test_concurrent_writers_first_alias_claim_wins(wiki_pg, wiki_pg_url):
    """Two threads, two psycopg connections, both try to insert alias 'RA'
    under different entity_ids. Exactly one row should land; both calls
    return cleanly (no exception, no deadlock).
    """
    barrier = threading.Barrier(2, timeout=5.0)

    def write_alias(entity_id: str, canonical: str):
        with psycopg.connect(wiki_pg_url) as conn:
            # Both threads ready before either writes — maximises collision odds.
            barrier.wait()
            with conn.transaction():
                insert_aliases_idempotent(conn, [(entity_id, canonical, ["RA"])])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [
            ex.submit(write_alias, "concept__rag", "RAG"),
            ex.submit(write_alias, "tool__rust_analyzer", "Rust Analyzer"),
        ]
        # Neither call should raise — ON CONFLICT swallows the duplicate.
        for f in concurrent.futures.as_completed(futures, timeout=10.0):
            f.result()  # re-raises if the worker raised

    # Exactly one row for alias 'RA' — the first writer's entity_id wins.
    rows = wiki_pg.execute(
        "SELECT entity_id, canonical_name FROM wiki.aliases WHERE alias = 'RA'"
    ).fetchall()
    assert len(rows) == 1
    winner_entity, winner_canonical = rows[0]
    assert winner_entity in ("concept__rag", "tool__rust_analyzer")
    if winner_entity == "concept__rag":
        assert winner_canonical == "RAG"
    else:
        assert winner_canonical == "Rust Analyzer"


@pytest.mark.timeout(15)
def test_many_concurrent_writers_no_deadlock(wiki_pg, wiki_pg_url):
    """Ten threads, ten different aliases, all writing to the same table at
    once. No deadlock, no exception, all 10 rows land. Exercises that
    psycopg's default isolation + INSERT ... ON CONFLICT scales without
    serializing the whole table."""
    n = 10
    barrier = threading.Barrier(n, timeout=5.0)

    def write_unique(idx: int):
        with psycopg.connect(wiki_pg_url) as conn:
            barrier.wait()
            with conn.transaction():
                insert_aliases_idempotent(
                    conn,
                    [(f"concept__e{idx}", f"Entity{idx}", [f"alias_{idx}"])],
                )

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(write_unique, range(n), timeout=10.0))

    # All 20 rows landed — n canonical_name rows + n alias_idx rows.
    count = wiki_pg.execute("SELECT count(*) FROM wiki.aliases").fetchone()[0]
    assert count == n * 2  # canonical and one alias per entity
