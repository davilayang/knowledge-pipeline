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
def test_high_contention_concurrent_writers_no_deadlock(wiki_pg, wiki_pg_url):
    """Ten threads divided into TWO groups racing on the same two aliases.
    The first writer in each group wins; the other 8 hit ON CONFLICT DO
    NOTHING. Test asserts no deadlock, no exception, exactly 2 rows land.

    Earlier version of this test wrote 10 disjoint aliases — that exercised
    no contention at all and would have passed even with ON CONFLICT removed.
    The current shape genuinely tests what the docstring claims: high-
    contention concurrent inserts don't serialize or deadlock.
    """
    n_per_group = 5
    n = n_per_group * 2
    barrier = threading.Barrier(n, timeout=5.0)

    def write_contended(idx: int):
        # Half the threads claim "shared_a", half claim "shared_b".
        # Each writer has a unique entity_id so the loser of each race is
        # silently dropped by ON CONFLICT (alias) DO NOTHING.
        target_alias = "shared_a" if idx < n_per_group else "shared_b"
        with psycopg.connect(wiki_pg_url) as conn:
            barrier.wait()
            with conn.transaction():
                insert_aliases_idempotent(
                    conn,
                    [(f"concept__e{idx}", f"Entity{idx}", [target_alias])],
                )

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(write_contended, range(n), timeout=10.0))

    # Each unique alias string lands once. shared_a + shared_b = 2 distinct
    # alias rows. Plus the canonical names — Entity0..Entity9 are all unique
    # so all 10 land. 12 rows total.
    rows = wiki_pg.execute("SELECT alias FROM wiki.aliases ORDER BY alias").fetchall()
    aliases = [r[0] for r in rows]
    assert aliases.count("shared_a") == 1
    assert aliases.count("shared_b") == 1
    # Canonical names landed too (Entity0..Entity9, 10 rows)
    assert sum(1 for a in aliases if a.startswith("Entity")) == 10
    assert len(rows) == 12
