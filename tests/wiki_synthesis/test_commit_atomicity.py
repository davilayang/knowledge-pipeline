"""persist() writes one atomic transaction — pages, page_sources, aliases,
and processed all land together or not at all.

If a processed row could land without its pages (or vice versa), downstream
consumers that watch for 'pages without a processed row' drift forever. Two
tests prove that *any* failure inside persist's `with conn:` block leaves all
tables unchanged — the .md files (written before persist, file-atomic) remain
on disk and get rewritten on the next run.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from domains.wiki.state import connect, get_page, get_processed_ids, snapshot_aliases
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    make_extraction,
    make_item,
    make_llm_call,
)


def _assert_all_empty(db_path: Path, *entity_ids: str) -> None:
    conn = connect(db_path)
    try:
        for eid in entity_ids:
            assert get_page(conn, eid) is None
        assert get_processed_ids(conn, status="ok") == set()
        assert get_processed_ids(conn, status="error") == set()
        assert snapshot_aliases(conn).entries == {}
    finally:
        conn.close()


def test_persist_rolls_back_when_insert_processed_fails(tmp_path: Path, wiki_db_path):
    """upsert_page + insert_aliases run successfully inside the txn;
    insert_processed raises; the whole txn rolls back."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = make_extraction("concept__rollback_a", "concept__rollback_b")

    def gen(prompt, *, system="", model=""):
        if "entity_id: concept__rollback_a" in prompt:
            return make_llm_call(content=build_synthesis_output("concept__rollback_a"))
        return make_llm_call(content=build_synthesis_output("concept__rollback_b"))

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch("workflows.wiki_synthesis.synthesize.generate_with_usage", side_effect=gen),
        patch(
            "workflows.wiki_synthesis.synthesize.insert_processed",
            side_effect=RuntimeError("simulated DB failure on processed insert"),
        ),
    ):
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            synthesize_item(
                make_item(item_id="rollback_test"), db_path=wiki_db_path, wiki_dir=wiki_dir
            )

    _assert_all_empty(wiki_db_path, "concept__rollback_a", "concept__rollback_b")

    # The .md files DO exist on disk (write_page runs before persist and is
    # file-atomic). They get rewritten on the next run. Documenting that here.
    assert (wiki_dir / "concept" / "rollback_a.md").exists()
    assert (wiki_dir / "concept" / "rollback_b.md").exists()


def test_persist_rolls_back_when_upsert_page_fails(tmp_path: Path, wiki_db_path):
    """upsert_page is the FIRST DB write inside the txn. If it raises, the txn
    aborts before insert_aliases or insert_processed run."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = make_extraction("concept__early_fail")

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("concept__early_fail")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.upsert_page",
            side_effect=RuntimeError("simulated DB failure on page upsert"),
        ),
    ):
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            synthesize_item(
                make_item(item_id="early_fail_test"), db_path=wiki_db_path, wiki_dir=wiki_dir
            )

    _assert_all_empty(wiki_db_path, "concept__early_fail")
