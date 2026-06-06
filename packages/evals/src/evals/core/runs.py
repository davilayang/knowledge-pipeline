"""RunRecord persistence to data/eval_runs/{kind}/{target}/{version}/{run_id}/run.json.

JSON layout mirrors Inspect AI's .eval file shape (results.scores nested under
results, samples list) so a future adapter pointing `inspect view` at our logs
is a small mapping job — not a rewrite of persistence.
"""

import dataclasses
import json
from pathlib import Path

from evals.core.types import RunRecord, VariantProvenance


def run_dir(*, root: Path, kind: str, target: str, version: str, run_id: str) -> Path:
    return Path(root) / kind / target / version / run_id


def _to_persistent_dict(rec: RunRecord) -> dict:
    """Reshape to nest scores under `results` so the file shape mirrors
    Inspect AI's `.eval` layout. `version` is encoded in the run-dir path,
    not duplicated inside the file."""
    raw = dataclasses.asdict(rec)
    scores = raw.pop("scores", [])
    samples = raw.pop("samples", [])
    return {**raw, "samples": samples, "results": {"scores": scores}}


def save_run(*, root: Path, version: str, record: RunRecord) -> Path:
    d = run_dir(
        root=root, kind=record.kind, target=record.target, version=version, run_id=record.run_id
    )
    d.mkdir(parents=True, exist_ok=True)
    path = d / "run.json"
    path.write_text(json.dumps(_to_persistent_dict(record), indent=2))
    return path


def load_run(*, root: Path, kind: str, target: str, version: str, run_id: str) -> RunRecord:
    path = run_dir(root=root, kind=kind, target=target, version=version, run_id=run_id) / "run.json"
    data = json.loads(path.read_text())
    # Flatten Inspect-compat back to the dataclass shape.
    flat = {**data, "scores": data["results"]["scores"], "samples": data["samples"]}
    flat.pop("results", None)
    flat["variant_provenance"] = VariantProvenance(**flat["variant_provenance"])
    # samples + scores stay as plain dicts/lists in this load path — Step 3 will
    # add typed-record rehydration when actual judges produce real outputs.
    return RunRecord(**flat)
