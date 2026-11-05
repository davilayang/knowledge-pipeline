"""``eval-search-score`` console script: score a saved search run on the frozen snapshot.

Reads runs harvested from newsletter-assistant's own search tools (the baseline);
runs from `POST /v1/search` need their own reader, which does not exist yet.

    uv run eval-search-score --run data/eval_runs/search/baseline_2026-11-01.json

Validates the dataset and snapshot first and fails closed: a missing snapshot, a
digest that differs from the manifest, files from different dataset versions, a
gold row the snapshot cannot back, or a run whose probes differ from the
dataset's stops the run before any number is printed.
"""

import argparse
import json
import sys
from pathlib import Path

from evals.search.dataset import load_gold, load_probes
from evals.search.legacy_run import hits_from_legacy_run
from evals.search.score import Snapshot, score_view
from evals.search.validate import validate

DATASETS = Path("packages/evals/datasets/search")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--snapshot", type=Path, default=Path("data/eval_snapshots/2026-10-31"))
    ap.add_argument("--datasets", type=Path, default=DATASETS)
    args = ap.parse_args(argv)

    p_head, probes = load_probes(args.datasets / "probes.jsonl")
    g_head, gold = load_gold(args.datasets / "gold.jsonl")
    manifest = json.loads((args.datasets / "snapshot.manifest").read_text())
    versions = {p_head.get("dataset_version"), g_head.get("dataset_version")}
    versions.add(manifest.get("dataset_version"))
    if len(versions) != 1 or None in versions:
        raise ValueError(f"probes, gold and manifest disagree on dataset_version: {versions}")
    snapshot = Snapshot(args.snapshot)
    validate(probes, gold, snapshot, manifest=manifest)

    run = json.loads(args.run.read_text())
    run_ids = [int(k) for k in run]
    if len(set(run_ids)) != len(run_ids) or set(run_ids) != {p.probe_id for p in probes}:
        sys.exit(f"run probes {sorted(run_ids)} differ from the dataset's")
    negatives = {p.probe_id for p in probes if p.negative}

    print(
        "| view | items handed over (mean) | recall, micro | recall, macro "
        "| precision | F2 | primary found |"
    )
    print("|---|---|---|---|---|---|---|")
    for view, hits in hits_from_legacy_run(run).items():
        s = score_view(hits, gold, snapshot, negatives=negatives)
        print(
            f"| {view} | {s.items / s.probes:.1f} | {s.found}/{s.total} = {s.recall:.0%} "
            f"| {s.macro_recall:.0%} | {s.precision:.0%} | {s.f2:.2f} "
            f"| {s.primary_found}/{s.primary_total} |"
        )


if __name__ == "__main__":
    main()
