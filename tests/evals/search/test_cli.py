import json
from pathlib import Path

import pytest
from evals.search.cli import main
from evals.search.score import snapshot_digests

DATASETS = Path(__file__).parents[3] / "packages/evals/datasets/search"


def _datasets(tmp_path: Path, snapshot_dir: Path, *, gold_version: str = "v1") -> Path:
    d = tmp_path / "datasets"
    d.mkdir()
    head = {"schema_version": 1, "dataset_version": "v1"}
    rows = [{"probe_id": 1, "text": "q", "caller": "both", "type": "sweep", "negative": None}]
    (d / "probes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [{**head, "fixture_kind": "search_probes"}, *rows])
    )
    (d / "gold.jsonl").write_text(
        json.dumps({**head, "fixture_kind": "search_gold", "dataset_version": gold_version})
    )
    manifest = {"dataset_version": "v1", "digests": snapshot_digests(snapshot_dir)}
    (d / "snapshot.manifest").write_text(json.dumps(manifest))
    return d


def _run(tmp_path: Path, probe_ids: list[str]) -> Path:
    rec = {"lanes": {k: [] for k in ("articles", "sessions", "notes", "briefs")}}
    rec["shipped"] = {"all": []}
    path = tmp_path / "run.json"
    path.write_text(json.dumps({pid: rec for pid in probe_ids}))
    return path


def test_a_run_with_a_probe_the_dataset_lacks_fails(tmp_path, snapshot_dir):
    d = _datasets(tmp_path, snapshot_dir)
    run = _run(tmp_path, ["1", "2"])
    with pytest.raises(SystemExit, match="probes"):
        main(["--run", str(run), "--snapshot", str(snapshot_dir), "--datasets", str(d)])


def test_gold_from_another_dataset_version_fails(tmp_path, snapshot_dir):
    d = _datasets(tmp_path, snapshot_dir, gold_version="v2")
    run = _run(tmp_path, ["1"])
    with pytest.raises(ValueError, match="dataset_version"):
        main(["--run", str(run), "--snapshot", str(snapshot_dir), "--datasets", str(d)])


def test_the_committed_dataset_agrees_with_its_own_versions():
    """Guards the shipped files without the snapshot: headers and manifest agree."""
    from evals.search.dataset import load_gold, load_probes

    p_head, _ = load_probes(DATASETS / "probes.jsonl")
    g_head, _ = load_gold(DATASETS / "gold.jsonl")
    manifest = json.loads((DATASETS / "snapshot.manifest").read_text())
    assert p_head["dataset_version"] == g_head["dataset_version"] == manifest["dataset_version"]
