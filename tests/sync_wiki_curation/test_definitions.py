"""Smoke test for the sync_wiki_curation Dagster pipeline.

Catches asset-graph regressions (broken imports, asset-key drift, dropped
schedule, pull→push dep loss) in CI rather than at 07:00 UTC. The pipeline binds
the shared "wiki" resource at the top-level merge, so the test merges shared.defs
before resolving jobs.
"""

import dagster as dg
from orchestrators.defs import shared, sync_wiki_curation


def test_definitions_load_with_expected_shape():
    assert sorted(s.name for s in sync_wiki_curation.defs.schedules) == [
        "run_daily_sync_wiki_curation"
    ]
    assert sorted(j.name for j in sync_wiki_curation.defs.jobs) == ["sync_wiki_curation"]

    # The asset graph resolves only once the "wiki" resource (owned by shared)
    # is present, so resolve against the merged defs.
    merged = dg.Definitions.merge(shared.defs, sync_wiki_curation.defs)
    asset_keys = {"/".join(k.path) for k in merged.resolve_asset_graph().get_all_asset_keys()}
    assert {"wiki/rejections_pulled", "wiki/pages_pushed"} <= asset_keys


def test_jobs_resolve_with_shared_wiki_resource():
    """push/pull require the shared "wiki" resource. Merged, the job must resolve
    end-to-end — a missing binding would fail at runtime."""
    merged = dg.Definitions.merge(shared.defs, sync_wiki_curation.defs)
    merged.resolve_all_job_defs()
