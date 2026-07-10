"""Smoke test — the synthesize_wiki DAG loads cleanly.

The wiki-write lane (attribute_claims → render_pages) carved out of
fetch_extract_queue; boundary = the queue.db ↔ wiki.db store seam. The pipeline
binds the shared "wiki" resource at the top-level merge, so the asset graph and
jobs resolve only against the merged defs.
"""

import dagster as dg
from orchestrators.defs import shared, synthesize_wiki
from orchestrators.defs.shared.resources import WikiResource
from workflows.wiki_synthesis.attributed_synthesis import (
    build_source_record,
    render_entity_pages,
    synthesize_source,
)

_CLAIMS_DOC = (
    "---\n"
    "item_id: https://medium.com/x\n"
    "content_date: '2026-03-01'\n"
    "---\n"
    "\n"
    "- [reported] GraphRAG uses a knowledge graph.\n"
    "- [reported] GraphRAG improves retrieval quality.\n"
)
_CANDIDATES_DOC = "GraphRAG — concept\n"


def test_dag_defs_loads():
    merged = dg.Definitions.merge(shared.defs, synthesize_wiki.defs)
    asset_keys = {k.to_user_string() for k in merged.resolve_asset_graph().get_all_asset_keys()}
    assert {
        "synthesize_wiki/attribute_claims",
        "synthesize_wiki/promote_notes",
        "synthesize_wiki/render_pages",
        "synthesize_wiki/build_index",
    } <= asset_keys


def test_build_index_asset_writes_both_files(tmp_path):
    from orchestrators.defs.synthesize_wiki.assets import build_index

    wiki = WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )
    synthesize_source(
        claims_doc=_CLAIMS_DOC,
        candidates_doc=_CANDIDATES_DOC,
        source=build_source_record(
            {
                "url": "https://medium.com/x",
                "canonical_url": "https://medium.com/x",
                "author": "Jane Doe",
                "content_date": "2026-03-01",
                "content_hash": "h",
            },
            now="2026-07-02T00:00:00+00:00",
        ),
        wiki_db_path=wiki.get_db_path(),
    )
    render_entity_pages(wiki_db_path=wiki.get_db_path(), wiki_dir=wiki.get_wiki_dir())

    result = dg.materialize([build_index], resources={"wiki": wiki})

    assert result.success
    assert (wiki.get_wiki_dir() / "_index" / "resolve.json").exists()
    assert (wiki.get_wiki_dir() / "index.md").exists()


def test_dag_has_synthesis_job():
    assert {j.name for j in synthesize_wiki.defs.jobs} == {"synthesize_wiki"}


def test_dag_exposes_daily_schedule():
    assert "run_daily_synthesize_wiki" in {s.name for s in synthesize_wiki.defs.schedules}


def test_job_resolves_with_shared_wiki_resource():
    # attribute_claims / render_pages require the shared "wiki" resource; merged,
    # the job must resolve end-to-end — a missing binding would fail at runtime.
    merged = dg.Definitions.merge(shared.defs, synthesize_wiki.defs)
    merged.resolve_all_job_defs()
