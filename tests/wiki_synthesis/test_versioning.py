"""Wiki page versioning (#47) — synthesize_item appends an immutable edition to
page_versions ONLY when the page's semantic content changes. The .md write and
the page_sources ledger update are unconditional (codex Phase-0: gate the
version append, not the file write). Drives the public synthesize_item with
mocked LLMs + a real wiki.db (same shape as test_num_sources)."""

from pathlib import Path

from domains.wiki.state import connect
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    make_extraction,
    make_item,
    make_llm_call,
)


def _run(item, wiki_db_path, wiki_dir, *, synthesis: str) -> None:
    from dataclasses import replace
    from unittest.mock import patch

    item = replace(item, title="RAG")  # entity in title → clears the salience gate
    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(make_extraction("RAG"), make_llm_call()),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=synthesis),
        ),
    ):
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)


def _run_multi(item, wiki_db_path, wiki_dir, *, names, synthesis_side_effect) -> None:
    """Drive synthesize_item with N extracted entities and a per-entity synthesis
    side_effect (a value or an Exception per synthesis call)."""
    from dataclasses import replace
    from unittest.mock import patch

    item = replace(item, title=" and ".join(names))  # all entities in title → salient
    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(make_extraction(*names), make_llm_call()),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            side_effect=synthesis_side_effect,
        ),
    ):
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)


def _versions(wiki_db_path):
    conn = connect(wiki_db_path)
    try:
        return conn.execute(
            "SELECT version, content, source_id, source_type FROM page_versions ORDER BY version"
        ).fetchall()
    finally:
        conn.close()


def _only_page(wiki_dir: Path) -> str:
    files = list(wiki_dir.glob("*.md"))
    assert len(files) == 1, f"expected one page, found {files}"
    return files[0].read_text(encoding="utf-8")


def test_first_synthesis_appends_v1(tmp_path: Path, wiki_db_path):
    """The first time an entity is synthesised, v1 is recorded with the full
    body and the provenance of the content item that created it."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    body = build_synthesis_output("RAG").replace("Body.", "First edition body.")
    _run(make_item(item_id="content_1"), wiki_db_path, wiki_dir, synthesis=body)

    rows = _versions(wiki_db_path)
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert "First edition body." in rows[0]["content"]
    assert rows[0]["source_id"] == "content_1"
    assert rows[0]["source_type"] == "raw_store"


def test_unchanged_resynthesis_appends_no_version_but_updates_ledger(tmp_path: Path, wiki_db_path):
    """A second article that surfaces the same entity but yields byte-identical
    semantic content is NOT a new edition — no version row is appended. The .md
    file and the page_sources ledger still update (num_sources → 2), proving the
    gate gates the version append, not the file write (codex Phase-0)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    body = build_synthesis_output("RAG")
    _run(make_item(item_id="content_1"), wiki_db_path, wiki_dir, synthesis=body)
    _run(make_item(item_id="content_2"), wiki_db_path, wiki_dir, synthesis=body)

    rows = _versions(wiki_db_path)
    assert len(rows) == 1  # still just v1 — content didn't change
    assert "num_sources: 2" in _only_page(wiki_dir)  # ledger + file still advanced


def test_changed_resynthesis_appends_v2_with_new_provenance(tmp_path: Path, wiki_db_path):
    """A re-synthesis that changes the body appends v2, tagged with the content
    item that triggered the change — the immutable forward history."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run(
        make_item(item_id="content_1"),
        wiki_db_path,
        wiki_dir,
        synthesis=build_synthesis_output("RAG").replace("Body.", "Original body."),
    )
    _run(
        make_item(item_id="content_2"),
        wiki_db_path,
        wiki_dir,
        synthesis=build_synthesis_output("RAG").replace("Body.", "Revised body, expanded."),
    )

    rows = _versions(wiki_db_path)
    assert [r["version"] for r in rows] == [1, 2]
    assert "Original body." in rows[0]["content"]
    assert "Revised body, expanded." in rows[1]["content"]
    assert rows[1]["source_id"] == "content_2"


def test_errored_entity_appends_no_version_while_sibling_does(tmp_path: Path, wiki_db_path):
    """One item extracting two entities where the second synthesis fails: only
    the succeeding entity gets a version row (and a page file). The errored
    entity bypasses the persist transaction entirely (excluded from successes)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run_multi(
        make_item(item_id="content_1"),
        wiki_db_path,
        wiki_dir,
        names=("RAG", "LLM"),
        synthesis_side_effect=[
            make_llm_call(content=build_synthesis_output("RAG")),
            RuntimeError("synthesis boom for the second entity"),
        ],
    )

    rows = _versions(wiki_db_path)
    assert len(rows) == 1  # only the succeeding entity versioned
    assert len(list(wiki_dir.glob("*.md"))) == 1  # only one page file written
