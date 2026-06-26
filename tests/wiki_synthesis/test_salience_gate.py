"""The deterministic salience gate inside synthesis: a tangential one-mention
source must NOT drive an entity's page (the measured 53%-low-salience problem).

Driven through the public synthesize_from_candidates with the synthesis LLM
mocked. A peripheral entity is still MINTED (a row, resolvable later) but gets no
page and no page_sources edge — the no-orphan invariant relaxes to "eventually
has a page" (codex: dropping it entirely risks a later entity-split).
"""

from pathlib import Path
from unittest.mock import patch

from domains.wiki.identity import Candidate
from domains.wiki.state import (
    connect,
    count_sources_for_entity,
    get_all_entities,
    get_all_pages,
)
from workflows.wiki_synthesis.synthesize import synthesize_from_candidates

from tests.wiki_synthesis._helpers import build_synthesis_output, make_item, make_llm_call


def _entities_by_name(db_path: Path) -> dict[str, str]:
    conn = connect(db_path)
    try:
        return {e.canonical_name: e.entity_id for e in get_all_entities(conn)}
    finally:
        conn.close()


def test_peripheral_source_is_minted_but_drives_no_page(tmp_path: Path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # "Salient" is in the title + named 3× → salient. "Peripheral" appears once,
    # not in the title → peripheral (the YOYO case).
    item = make_item(
        item_id="content_gate",
        title="All about Salient",
        text="Salient leads here. Salient does things. Salient again. Peripheral appeared once.",
        source_ref="raw_store:content_gate",
    )
    candidates = [
        Candidate(name="Salient", page_type="concept"),
        Candidate(name="Peripheral", page_type="concept"),
    ]
    synthesis_calls = 0

    def counting_generate(prompt, *, system="", model=""):
        nonlocal synthesis_calls
        synthesis_calls += 1
        return make_llm_call(content=build_synthesis_output("Salient"))

    with patch(
        "workflows.wiki_synthesis.synthesize.generate_with_usage",
        side_effect=counting_generate,
    ):
        res = synthesize_from_candidates(item, candidates, db_path=wiki_db_path, wiki_dir=wiki_dir)

    assert res["status"] == "ok"
    # Only the salient entity is synthesized — one LLM call, one page, one file.
    assert synthesis_calls == 1
    conn = connect(wiki_db_path)
    try:
        assert len(get_all_pages(conn)) == 1
    finally:
        conn.close()
    assert len(list(wiki_dir.glob("*.md"))) == 1

    # BUT the peripheral entity is still minted (a row) — resolvable when a future
    # article IS about it; it just carries no page_sources edge for this item.
    entities = _entities_by_name(wiki_db_path)
    assert set(entities) == {"Salient", "Peripheral"}
    conn = connect(wiki_db_path)
    try:
        assert count_sources_for_entity(conn, entities["Salient"]) == 1
        assert count_sources_for_entity(conn, entities["Peripheral"]) == 0
    finally:
        conn.close()
