"""Smoke test — the synthesize_wiki DAG loads cleanly.

The wiki-write lane (attribute_claims → render_pages) carved out of
fetch_extract_queue; boundary = the queue.db ↔ wiki.db store seam. The pipeline
binds the shared "wiki" resource at the top-level merge, so the asset graph and
jobs resolve only against the merged defs.
"""

import dagster as dg
from orchestrators.defs import shared, synthesize_wiki


def test_dag_defs_loads():
    merged = dg.Definitions.merge(shared.defs, synthesize_wiki.defs)
    asset_keys = {k.to_user_string() for k in merged.resolve_asset_graph().get_all_asset_keys()}
    assert {"synthesize_wiki/attribute_claims", "synthesize_wiki/render_pages"} <= asset_keys


def test_dag_has_synthesis_job():
    assert {j.name for j in synthesize_wiki.defs.jobs} == {"synthesize_wiki"}


def test_dag_exposes_daily_schedule():
    assert "run_daily_synthesize_wiki" in {s.name for s in synthesize_wiki.defs.schedules}


def test_job_resolves_with_shared_wiki_resource():
    # attribute_claims / render_pages require the shared "wiki" resource; merged,
    # the job must resolve end-to-end — a missing binding would fail at runtime.
    merged = dg.Definitions.merge(shared.defs, synthesize_wiki.defs)
    merged.resolve_all_job_defs()
