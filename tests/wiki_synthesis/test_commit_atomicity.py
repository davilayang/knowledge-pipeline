"""Crash-safety of the persist sequence: _persist_graph (entities + pages +
page_sources + aliases) is one atomic transaction; the .md files are written
next; the processed row is written LAST.

Two guarantees:
  - A failure INSIDE _persist_graph rolls the whole graph back and writes no
    files and no processed row (nothing partially lands).
  - A failure AFTER the graph commits but before the processed row leaves a
    RECOVERABLE state: the graph + files are durable, but the item stays
    un-processed, so `pending` re-queues it and the (idempotent) write retries —
    rather than stranding a 'ok' page whose file never got written.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from domains.wiki.state import (
    connect,
    get_all_entities,
    get_all_pages,
    get_processed_ids,
    snapshot_aliases,
)
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    make_extraction,
    make_item,
    make_llm_call,
)


def test_graph_rolls_back_when_insert_entity_fails(tmp_path: Path, wiki_db_path):
    """insert_entity is the FIRST DB write inside _persist_graph (FK parents
    first). If it raises, the txn aborts before pages / aliases — and no files
    or processed row land (files are written only after the graph commits)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = make_extraction("Early Fail")

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("Early Fail")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.insert_entity",
            side_effect=RuntimeError("simulated DB failure on entity insert"),
        ),
    ):
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            synthesize_item(
                make_item(item_id="early_fail_test"), db_path=wiki_db_path, wiki_dir=wiki_dir
            )

    conn = connect(wiki_db_path)
    try:
        assert get_all_entities(conn) == []
        assert get_all_pages(conn) == []
        assert get_processed_ids(conn, status="ok") == set()
        assert snapshot_aliases(conn).entries == {}
    finally:
        conn.close()
    assert list(wiki_dir.glob("*.md")) == []


def test_processed_failure_leaves_recoverable_state(tmp_path: Path, wiki_db_path):
    """If marking the item processed fails AFTER the graph commits and the files
    are written, the entities/pages/files are durable but the item stays
    un-processed — so `pending` (which excludes only ok/skipped) re-queues it."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = make_extraction("Recoverable")

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("Recoverable")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.insert_processed",
            side_effect=RuntimeError("simulated DB failure on processed insert"),
        ),
    ):
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            synthesize_item(
                make_item(item_id="recoverable_test"), db_path=wiki_db_path, wiki_dir=wiki_dir
            )

    conn = connect(wiki_db_path)
    try:
        # Graph committed: the entity + page are durable.
        assert {e.canonical_name for e in get_all_entities(conn)} == {"Recoverable"}
        assert len(get_all_pages(conn)) == 1
        # But the item is NOT marked processed → pending re-queues it next run.
        assert get_processed_ids(conn, status="ok") == set()
    finally:
        conn.close()
    # The .md file was written before the (failed) processed step.
    assert len(list(wiki_dir.glob("*.md"))) == 1
