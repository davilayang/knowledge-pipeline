"""Smoke tests for the populate_vector_store Dagster pipeline.

Mirrors backup_readings / fetch_extract_queue — asset-graph regressions
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
        "vector_store/wiki",
        "vector_store/briefings",
    }
    assert sorted(s.name for s in defs.schedules) == ["run_populate_vector_store"]
    assert sorted(j.name for j in defs.jobs) == ["populate_vector_store"]


def test_articles_lane_reads_the_renamed_articles_db(tmp_path):
    """The contents lane embeds newsletter-assistant's articles store, renamed
    raw_store.db -> corpus.db in NA's 0.46.0 three-DB topology change (deployed
    2026-07-11). Pointing at the old name silently yields an empty lane."""
    from orchestrators.defs.populate_vector_store.resources import SourcesResource

    sources = SourcesResource(backup_source_dir=str(tmp_path))
    assert sources.raw_store()._db_path == tmp_path / "corpus.db"


def test_schedule_is_armed_by_default():
    """The embedding job is the only writer of every ChromaDB collection recall
    reads. A STOPPED default meant any loss of persisted Dagster schedule state
    disarmed it silently — which happened on 2026-05-16 and left recall serving
    a three-month-stale index (14 of 533 articles, no session after that date,
    zero briefs, no wiki collection at all) with no error anywhere."""
    import dagster as dg

    (schedule,) = defs.schedules
    assert schedule.default_status == dg.DefaultScheduleStatus.RUNNING
