"""Wiki entity_relations accumulation (#54) — synthesize_item records the
co-occurrence edges among the entities an item surfaces, in BOTH directions,
tagged with the contributing item. Drives the public synthesize_item with
mocked LLMs + a real wiki.db."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from domains.wiki.state import connect
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    make_extraction,
    make_item,
    make_llm_call,
)


def _run(item, wiki_db_path, wiki_dir, *, names):
    # Put every co-occurring entity in the title so all clear the salience gate —
    # co-occurrence edges only form among salient entities.
    item = replace(item, title=" and ".join(names))
    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(make_extraction(*names), make_llm_call()),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("X")),
        ),
    ):
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)


def _related_line(wiki_dir, wiki_db_path, canonical_name):
    """The `related:` frontmatter line of the page for entity `canonical_name`
    (located via the entities→pages join, since the mocked synthesis output
    doesn't carry the real title)."""
    conn = connect(wiki_db_path)
    try:
        row = conn.execute(
            "SELECT p.file_path FROM pages p JOIN entities e ON e.entity_id = p.entity_id "
            "WHERE e.canonical_name = ?",
            (canonical_name,),
        ).fetchone()
    finally:
        conn.close()
    assert row, f"no page for {canonical_name}"
    fm = (wiki_dir / row["file_path"]).read_text(encoding="utf-8").split("---")[1]
    return next(line for line in fm.splitlines() if line.startswith("related:"))


def _edges_by_name(wiki_db_path):
    conn = connect(wiki_db_path)
    try:
        rows = conn.execute(
            """
            SELECT a.canonical_name AS src, b.canonical_name AS dst, er.item_id
            FROM entity_relations er
            JOIN entities a ON a.entity_id = er.entity_id
            JOIN entities b ON b.entity_id = er.related_entity_id
            """
        ).fetchall()
        return {(r["src"], r["dst"], r["item_id"]) for r in rows}
    finally:
        conn.close()


def test_co_occurring_entities_get_both_direction_edges(tmp_path: Path, wiki_db_path):
    """An item surfacing {RAG, LLM} records both RAG→LLM and LLM→RAG, tagged
    with the item that co-mentioned them."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run(make_item(item_id="content_1"), wiki_db_path, wiki_dir, names=("RAG", "LLM"))

    assert _edges_by_name(wiki_db_path) == {
        ("RAG", "LLM", "content_1"),
        ("LLM", "RAG", "content_1"),
    }


def test_single_entity_item_records_no_edges(tmp_path: Path, wiki_db_path):
    """A solo entity has no co-occurrence — no edges (and prior edges, if any,
    are untouched since this is an accumulating ledger)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run(make_item(item_id="content_1"), wiki_db_path, wiki_dir, names=("RAG",))

    assert _edges_by_name(wiki_db_path) == set()


def test_related_frontmatter_accumulates_across_articles(tmp_path: Path, wiki_db_path):
    """RAG's rendered `related` accumulates every co-mentioned entity across
    articles (from the entity_relations ledger), not just the latest article's
    siblings. Article 1: {RAG, LLM}; article 2: {RAG, Chroma} → RAG relates to
    BOTH LLM and Chroma."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    _run(make_item(item_id="content_1"), wiki_db_path, wiki_dir, names=("RAG", "LLM"))
    _run(make_item(item_id="content_2"), wiki_db_path, wiki_dir, names=("RAG", "Chroma"))

    related = _related_line(wiki_dir, wiki_db_path, "RAG")
    # Two distinct related entity ids (LLM from art1 + Chroma from art2).
    assert related.count("e_") == 2
