"""The commit node's transaction is atomic — wiki.pages, wiki.aliases,
and wiki.processed all land together or not at all.

Plan §Migration phases step 9 hard rule: "Workflow's terminal node writes
wiki.processed row in the same transaction as the final page write." If
that's violated, downstream consumers (Dagster sensors that watch for
'pages without a processed row' or vice versa) see drift and fire false
alarms forever.

Two tests:
  - rollback when insert_processed raises mid-txn → no rows land anywhere.
  - rollback when upsert_page raises mid-txn → still nothing lands.

Both prove that *any* failure inside the commit's `with conn.transaction():`
block leaves all three tables unchanged.

Plan reference: ai-plannings/2026-05-02_workspace-phase-b-pr2.md → Property 4.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from domains.wiki.state import (
    get_page,
    get_processed_ids,
    snapshot_aliases,
)
from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    make_extraction,
    make_item,
)
from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph


def _setup(tmp_path: Path) -> Path:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    return wiki_dir


def test_commit_txn_rolls_back_when_insert_processed_fails(
    tmp_path: Path, wiki_pg, wiki_pg_url
):
    """upsert_page and insert_aliases run successfully inside the txn;
    insert_processed raises; the whole txn rolls back. None of pages,
    aliases, or processed rows should be visible after."""
    wiki_dir = _setup(tmp_path)

    extraction = make_extraction("concept__rollback_a", "concept__rollback_b")

    def gen(prompt, *, system="", model=""):
        # Pick the right entity based on which prompt this is
        if "rollback_a" in prompt and "entity_id: concept__rollback_a" in prompt:
            return build_synthesis_output("concept__rollback_a")
        return build_synthesis_output("concept__rollback_b")

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=gen,
        ),
        patch(
            "workflows.wiki_synthesis.nodes.insert_processed",
            side_effect=RuntimeError("simulated DB failure on processed insert"),
        ),
    ):
        graph = build_wiki_synthesis_graph().compile()
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            graph.invoke(
                {
                    "item": make_item(item_id="rollback_test"),
                    "db_url": wiki_pg_url,
                    "wiki_dir": str(wiki_dir),
                }
            )

    # No rows in any of the three tables — full rollback.
    assert get_page(wiki_pg, "concept__rollback_a") is None
    assert get_page(wiki_pg, "concept__rollback_b") is None
    assert get_processed_ids(wiki_pg, status="ok") == set()
    assert get_processed_ids(wiki_pg, status="error") == set()
    assert snapshot_aliases(wiki_pg).entries == {}

    # The .md files DO exist on disk (write_page is outside the txn boundary
    # and is file-atomic). They get rewritten by replay if commit succeeds
    # next time. Documenting that intentional behavior here.
    assert (wiki_dir / "concept" / "rollback_a.md").exists()
    assert (wiki_dir / "concept" / "rollback_b.md").exists()


def test_commit_txn_rolls_back_when_upsert_page_fails(
    tmp_path: Path, wiki_pg, wiki_pg_url
):
    """upsert_page is the FIRST DB write inside the txn. If it raises,
    the txn aborts before insert_aliases or insert_processed runs."""
    wiki_dir = _setup(tmp_path)

    extraction = make_extraction("concept__early_fail")

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            return_value=build_synthesis_output("concept__early_fail"),
        ),
        patch(
            "workflows.wiki_synthesis.nodes.upsert_page",
            side_effect=RuntimeError("simulated DB failure on page upsert"),
        ),
    ):
        graph = build_wiki_synthesis_graph().compile()
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            graph.invoke(
                {
                    "item": make_item(item_id="early_fail_test"),
                    "db_url": wiki_pg_url,
                    "wiki_dir": str(wiki_dir),
                }
            )

    assert get_page(wiki_pg, "concept__early_fail") is None
    assert get_processed_ids(wiki_pg, status="ok") == set()
    assert get_processed_ids(wiki_pg, status="error") == set()
    assert snapshot_aliases(wiki_pg).entries == {}
