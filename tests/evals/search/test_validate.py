import pytest
from evals.search.dataset import GoldNeed, Probe
from evals.search.score import Snapshot
from evals.search.validate import validate

A1 = "kos://piece/article/medium::https:%2F%2Fa.example%2Fpost-1"
PROBES = [Probe(1, "ask", "both", "sweep", None), Probe(2, "neg ask", "both", "sweep", "plain")]


def test_a_gold_uri_the_snapshot_does_not_hold_fails(snapshot_dir):
    gold = [GoldNeed("p01-g01", 1, 2, False, False, "x", ("kos://piece/article/web::nowhere",))]
    with pytest.raises(ValueError, match="does not resolve"):
        validate(PROBES, gold, Snapshot(snapshot_dir))


def test_a_negative_probe_with_gold_fails(snapshot_dir):
    gold = [GoldNeed("p02-g01", 2, 2, False, False, "x", (A1,))]
    with pytest.raises(ValueError, match="negative"):
        validate(PROBES, gold, Snapshot(snapshot_dir))


def test_one_article_in_two_needs_of_a_probe_fails(snapshot_dir):
    gold = [
        GoldNeed("p01-g01", 1, 2, False, False, "x", (A1,)),
        GoldNeed("p01-g02", 1, 2, False, False, "y", (A1,)),
    ]
    with pytest.raises(ValueError, match="two needs"):
        validate(PROBES, gold, Snapshot(snapshot_dir))


def test_a_snapshot_that_differs_from_its_manifest_fails(snapshot_dir):
    from evals.search.score import snapshot_digests

    manifest = {"digests": snapshot_digests(snapshot_dir)}
    validate(PROBES, [], Snapshot(snapshot_dir), manifest=manifest)
    (snapshot_dir / "notes_x/notes/2026-01-01_x.md").write_text("changed")
    with pytest.raises(ValueError, match="notes"):
        validate(PROBES, [], Snapshot(snapshot_dir), manifest=manifest)


def test_a_manifest_missing_a_lane_fails(snapshot_dir):
    from evals.search.score import snapshot_digests

    digests = snapshot_digests(snapshot_dir)
    del digests["wiki"]
    with pytest.raises(ValueError, match="lanes"):
        validate(PROBES, [], Snapshot(snapshot_dir), manifest={"digests": digests})


def test_an_article_uri_not_in_canonical_encoding_fails(snapshot_dir):
    raw = "kos://piece/article/medium::https://a.example/post-1"
    gold = [GoldNeed("p01-g01", 1, 2, False, False, "x", (raw,))]
    with pytest.raises(ValueError, match="canonical"):
        validate(PROBES, gold, Snapshot(snapshot_dir))


@pytest.mark.parametrize(
    "need, message",
    [
        (GoldNeed("p09-g01", 9, 2, False, False, "x", (A1,)), "no such probe"),
        (GoldNeed("p01-g01", 1, 3, False, False, "x", (A1,)), "grade"),
        (GoldNeed("p01-g01", 1, 2, False, False, "x", ()), "no uris"),
    ],
)
def test_a_malformed_gold_row_fails(snapshot_dir, need, message):
    with pytest.raises(ValueError, match=message):
        validate(PROBES, [need], Snapshot(snapshot_dir))


def test_a_repeated_gold_id_fails(snapshot_dir):
    need = GoldNeed("p01-g01", 1, 2, False, False, "x", (A1,))
    with pytest.raises(ValueError, match="repeated"):
        validate(PROBES, [need, need], Snapshot(snapshot_dir))
