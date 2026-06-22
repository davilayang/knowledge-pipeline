"""Smoke tests for the synthesize_wiki Dagster pipeline.

Mirrors backup_readings/test_definitions.py — runs on every commit so
asset-graph regressions (broken imports, asset-key drift, dep-graph
mistakes, dropped schedule) fail in CI rather than at 06:00 UTC.
"""

from orchestrators.defs.synthesize_wiki import defs


def test_definitions_load_with_expected_shape():
    asset_keys = {"/".join(k.path) for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert asset_keys == {
        # snapshots/raw_store is an external upstream owned by
        # backup_readings; surfaces as an implicit node here via AssetDep.
        "snapshots/raw_store",
        "wiki/pending",
        "wiki/extracted",
        "wiki/synthesized",
        "wiki/index",
        "wiki/aliases_index",
    }
    assert sorted(s.name for s in defs.schedules) == ["run_daily_synthesize_wiki"]
    assert sorted(j.name for j in defs.jobs) == ["synthesize_wiki"]
