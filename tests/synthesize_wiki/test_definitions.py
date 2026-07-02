"""Smoke tests for the synthesize_wiki Dagster pipeline.

Mirrors backup_readings/test_definitions.py — runs on every commit so
asset-graph regressions (broken imports, asset-key drift, dep-graph
mistakes, dropped schedule) fail in CI rather than at 06:00 UTC.
"""

import dagster as dg
from orchestrators.defs import shared, synthesize_wiki

defs = synthesize_wiki.defs


def test_definitions_load_with_expected_shape():
    # "wiki" is owned by shared now, so merge it before resolving the asset graph
    # (merging also brings shared's raw_store_copy, hence a subset check).
    merged = dg.Definitions.merge(shared.defs, synthesize_wiki.defs)
    asset_keys = {"/".join(k.path) for k in merged.resolve_asset_graph().get_all_asset_keys()}
    assert {
        "wiki/pending",
        "wiki/extracted",
        "wiki/synthesized",
        "wiki/index",
        "wiki/aliases_index",
    } <= asset_keys
    assert sorted(s.name for s in defs.schedules) == ["run_daily_synthesize_wiki"]
    assert sorted(j.name for j in defs.jobs) == ["synthesize_wiki"]
