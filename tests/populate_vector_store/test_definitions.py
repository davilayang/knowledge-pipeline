"""Smoke tests for the populate_vector_store Dagster pipeline.

Mirrors backup_readings / synthesize_wiki — asset-graph regressions
(broken imports, asset-key drift, dropped schedule) fail in CI rather
than at the first manual launch.
"""

from orchestrators.defs.populate_vector_store import defs


def test_definitions_load_with_expected_shape():
    asset_keys = {"/".join(k.path) for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert asset_keys == {
        "vector_store/pending",
        "vector_store/contents",
        "vector_store/conversations",
        "vector_store/notes",
    }
    assert sorted(s.name for s in defs.schedules) == ["run_populate_vector_store"]
    assert sorted(j.name for j in defs.jobs) == ["populate_vector_store"]


def test_schedule_lands_paused():
    import dagster as dg

    (schedule,) = defs.schedules
    assert schedule.default_status == dg.DefaultScheduleStatus.STOPPED
