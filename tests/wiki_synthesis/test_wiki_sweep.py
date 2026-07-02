"""Tests for the wiki-synthesis attribute sweep — the unpartitioned pass that
turns queue.db's stored extraction docs into wiki.db attributed claims.

Seeds a real queue.db (queue_items + extraction_calls) and runs the sweep against
a fresh wiki.db. A single unambiguous mention needs no LLM, so these run offline;
the ambiguous fail-soft case injects a raising `attribute_subjects` mapper.
"""

from pathlib import Path

from domains.queue_store.sources import (
    create_schema,
    record_candidates,
    record_claims,
    upsert_fetched,
)
from domains.wiki.state import connection, get_all_entities
from workflows.wiki_synthesis.wiki_sweep import run_attribute_sweep

_CLAIMS_TWO = (
    "---\n"
    "item_id: https://medium.com/x\n"
    "content_date: '2026-03-01'\n"
    "---\n"
    "\n"
    "- [reported] GraphRAG uses a knowledge graph.\n"
    "- [opinion] GraphRAG will replace naive RAG.\n"
)
_CANDIDATES = "GraphRAG — concept\n"

# An ambiguous claim (two mentions) forces the subject-attribution mapper.
_CLAIMS_AMBIGUOUS = (
    "---\n" "item_id: https://medium.com/bad\n" "---\n" "\n" "- [reported] Foo beats Bar.\n"
)
_CANDIDATES_TWO = "Foo — concept\nBar — concept\n"


def _seed(
    queue_db: Path,
    *,
    page_id: str,
    url: str,
    claims_doc: str,
    candidates_doc: str | None,
) -> None:
    create_schema(db_path=queue_db)
    upsert_fetched(
        db_path=queue_db,
        notion_page_id=page_id,
        url=url,
        raw_content="body",
        fetch_tier="t",
        fetch_tier_log=[],
        fetched_content_char_count=4,
        content_hash="h",
        title="T",
        author="Jane Doe",
        content_date="2026-03-01",
    )
    record_claims(
        db_path=queue_db,
        notion_page_id=page_id,
        output=claims_doc,
        prompt_label="extract_claims_system_v1",
        prompt_sha256="a" * 64,
        model="m",
        tokens_in=1,
        tokens_out=1,
    )
    if candidates_doc is not None:
        record_candidates(
            db_path=queue_db,
            notion_page_id=page_id,
            output=candidates_doc,
            prompt_label="extract_entities_v1",
            prompt_sha256="a" * 64,
            model="m",
            tokens_in=1,
            tokens_out=1,
        )


def _raise(*_args):
    raise RuntimeError("subject-attribution unavailable")


def test_sweep_persists_ready_source_then_skips_unchanged(tmp_path, wiki_db_path):
    queue_db = tmp_path / "queue.db"
    _seed(
        queue_db,
        page_id="pg1",
        url="https://medium.com/x",
        claims_doc=_CLAIMS_TWO,
        candidates_doc=_CANDIDATES,
    )

    r1 = run_attribute_sweep(queue_db_path=queue_db, wiki_db_path=wiki_db_path)
    assert r1.new_sources == ["https://medium.com/x"]
    assert r1.persisted == 1
    assert r1.skipped_unchanged == []
    with connection(wiki_db_path) as conn:
        assert [e.canonical_name for e in get_all_entities(conn)] == ["GraphRAG"]

    # Second run — nothing re-extracted, watermark holds → skipped.
    r2 = run_attribute_sweep(queue_db_path=queue_db, wiki_db_path=wiki_db_path)
    assert r2.persisted == 0
    assert r2.skipped_unchanged == ["https://medium.com/x"]


def test_sweep_reprocesses_when_extraction_newer_than_watermark(tmp_path, wiki_db_path):
    queue_db = tmp_path / "queue.db"
    _seed(
        queue_db,
        page_id="pg1",
        url="https://medium.com/x",
        claims_doc=_CLAIMS_TWO,
        candidates_doc=_CANDIDATES,
    )
    run_attribute_sweep(queue_db_path=queue_db, wiki_db_path=wiki_db_path)

    # Backdate the stored watermark so the current extraction docs look newer.
    with connection(wiki_db_path) as conn, conn:
        conn.execute("UPDATE sources SET synthesized_at = '2000-01-01T00:00:00+00:00'")

    r = run_attribute_sweep(queue_db_path=queue_db, wiki_db_path=wiki_db_path)
    assert r.persisted == 1
    assert r.changed_sources == ["https://medium.com/x"]
    assert r.new_sources == []


def test_sweep_fail_soft_isolates_a_bad_source(tmp_path, wiki_db_path):
    # One good (unambiguous, no LLM) source and one that routes to the injected
    # subject-attribution mapper, which raises. The sweep persists the good one and
    # records the failure without aborting.
    queue_db = tmp_path / "queue.db"
    _seed(
        queue_db,
        page_id="pg1",
        url="https://medium.com/x",
        claims_doc=_CLAIMS_TWO,
        candidates_doc=_CANDIDATES,
    )
    _seed(
        queue_db,
        page_id="pg2",
        url="https://medium.com/bad",
        claims_doc=_CLAIMS_AMBIGUOUS,
        candidates_doc=_CANDIDATES_TWO,
    )

    r = run_attribute_sweep(
        queue_db_path=queue_db, wiki_db_path=wiki_db_path, attribute_subjects=_raise
    )
    assert r.new_sources == ["https://medium.com/x"]
    assert list(r.failed) == ["https://medium.com/bad"]
    assert "RuntimeError" in r.failed["https://medium.com/bad"]


def test_sweep_dedupes_pages_sharing_a_content_key(tmp_path, wiki_db_path):
    # Two queue rows with the same canonical URL (restored/duplicate) map to ONE
    # content_key. The sweep collapses them to a single source so they can't
    # overwrite each other's claims or regress the watermark within a run.
    queue_db = tmp_path / "queue.db"
    _seed(
        queue_db,
        page_id="pg1",
        url="https://medium.com/x",
        claims_doc=_CLAIMS_TWO,
        candidates_doc=_CANDIDATES,
    )
    _seed(
        queue_db,
        page_id="pg2",
        url="https://medium.com/x",
        claims_doc=_CLAIMS_TWO,
        candidates_doc=_CANDIDATES,
    )

    r = run_attribute_sweep(queue_db_path=queue_db, wiki_db_path=wiki_db_path)
    assert r.new_sources == ["https://medium.com/x"]
    assert r.persisted == 1


def test_sweep_reports_partial_extraction(tmp_path, wiki_db_path):
    # A page with only a claims doc (no entities) can't be swept — surfaced as
    # partial_extraction observability, not persisted.
    queue_db = tmp_path / "queue.db"
    _seed(
        queue_db,
        page_id="pg1",
        url="https://medium.com/x",
        claims_doc=_CLAIMS_TWO,
        candidates_doc=None,
    )

    r = run_attribute_sweep(queue_db_path=queue_db, wiki_db_path=wiki_db_path)
    assert r.persisted == 0
    assert r.partial_extraction == ["pg1"]
