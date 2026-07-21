"""Shared eval run-layer for the variant → FixtureRun → score shape.

`run_and_report` runs one variant over a fixture set, scores each fixture,
aggregates per-field + stratified, and persists a RunRecord carrying the
RunManifest in `config`. Content-type / content-shape stratification is
duck-typed (getattr) so this substrate stays harness-agnostic — core never
imports a per-pipeline fixture type.

Only the extraction / narrative-coverage harnesses run *through* this runner —
their `variant.run(fixture) -> FixtureRun` shape maps onto it directly. The
retrieval (index-then-query) and extract-claims (source-level aggregation)
harnesses keep their own run models; they share only the `RunManifest`
provenance envelope, not this runner. `RunManifest` is a provenance standard
across all evals, not a unified run model.
"""

import secrets
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from evals.core.manifest import RunManifest
from evals.core.runs import save_run
from evals.core.types import FieldScore, FixtureRun, RunRecord, RunStatus, ScoreReport
from evals.core.variants import Variant

_DEFAULT_DATA_ROOT = Path("data/eval_runs")
_RECORD_VERSION = "v1"


class ScorerProtocol(Protocol):
    name: str

    def score_run(self, *, fixture: Any, run: FixtureRun) -> FieldScore: ...


def run_and_report(
    *,
    variant: Variant,
    fixtures: Sequence[Any],
    scorer: ScorerProtocol,
    target: str,
    manifest: RunManifest | None = None,
    kind: str = "benchmark",
    fixture_set: str = "<inline>",
    data_root: Path | None = None,
    persist: bool = True,
) -> RunRecord:
    """Run a variant over fixtures, score each, aggregate per-field + stratified.

    `manifest` rides in RunRecord.config. Optional during migration; becomes
    required once every caller supplies one.
    ponytail: manifest optional bridge — make required in P2 when all paths pass one.
    """
    started = datetime.now(UTC).isoformat()
    samples: list[FixtureRun] = []
    per_field_acc: dict[str, list[float]] = defaultdict(list)
    per_type_overall: dict[str, list[float]] = defaultdict(list)
    per_shape_overall: dict[str, list[float]] = defaultdict(list)
    for fix in fixtures:
        fr = variant.run(fix)
        samples.append(fr)
        if fr.status != RunStatus.SUCCESS or fr.output is None:
            continue
        score = scorer.score_run(fixture=fix, run=fr)
        for field, v in score.value.items():
            per_field_acc[field].append(v)
        overall = score.value.get("__overall__", 0.0)
        content_type = getattr(fix, "content_type", None)
        if content_type is not None:
            per_type_overall[content_type].append(overall)
        content_shape = getattr(fix, "content_shape", None)
        if content_shape is not None:
            per_shape_overall[content_shape].append(overall)
    completed = datetime.now(UTC).isoformat()

    metrics = {field: sum(vs) / len(vs) for field, vs in per_field_acc.items() if vs}
    stratifications: dict[str, dict[str, float]] = {
        "by_content_type": {ct: sum(vs) / len(vs) for ct, vs in per_type_overall.items() if vs}
    }
    if per_shape_overall:
        stratifications["by_content_shape"] = {
            s: sum(vs) / len(vs) for s, vs in per_shape_overall.items() if vs
        }
    sr = ScoreReport(
        scorer_name=scorer.name,
        metrics=metrics,
        stratifications=stratifications,
        sample_count=len(samples),
    )
    rec = RunRecord(
        run_id=_ulid(),
        kind=kind,
        target=target,
        variant_name=variant.name,
        variant_config=variant.config,
        variant_provenance=variant.provenance,
        fixture_set=fixture_set,
        fixture_anchor=None,
        started_at=started,
        completed_at=completed,
        samples=samples,
        scores=[sr],
        config=asdict(manifest) if manifest is not None else {},
    )
    if persist:
        root = Path(data_root) if data_root is not None else _DEFAULT_DATA_ROOT
        save_run(root=root, version=_RECORD_VERSION, record=rec)
    return rec


@dataclass(frozen=True)
class RepeatedReport:
    """Aggregate over N full re-runs — the LLM-judge-noise envelope.

    `mean`/`lo`/`hi`/`per_run` track the aggregate `__overall__` metric across
    runs; `by_stratum` averages every stratification dimension (by_content_type,
    by_content_shape, …) across the same runs.
    """

    records: list[RunRecord]
    mean: float
    lo: float
    hi: float
    per_run: list[float]
    by_stratum: dict[str, dict[str, float]]


def run_repeated(
    *,
    variant: Variant,
    fixtures: Sequence[Any],
    scorer: ScorerProtocol,
    manifest: RunManifest | None = None,
    runs: int = 3,
    **kw: Any,
) -> RepeatedReport:
    """Run the benchmark `runs` times; return the mean + observed range.

    Each re-run re-runs the variant AND re-scores, so N runs capture total
    variance (extraction + judge). Promoted out of the coverage CLI so every
    noisy-judge harness averages the same way.
    """
    records = [
        run_and_report(variant=variant, fixtures=fixtures, scorer=scorer, manifest=manifest, **kw)
        for _ in range(runs)
    ]
    per_run = [r.scores[0].metrics.get("__overall__", 0.0) for r in records]
    strata_keys = set().union(*(r.scores[0].stratifications.keys() for r in records))
    by_stratum: dict[str, dict[str, float]] = {}
    for dim in strata_keys:
        buckets = set().union(*(r.scores[0].stratifications.get(dim, {}) for r in records))
        by_stratum[dim] = {
            b: _mean([r.scores[0].stratifications.get(dim, {}).get(b, 0.0) for r in records])
            for b in buckets
        }
    return RepeatedReport(
        records=records,
        mean=_mean(per_run),
        lo=min(per_run) if per_run else 0.0,
        hi=max(per_run) if per_run else 0.0,
        per_run=per_run,
        by_stratum=by_stratum,
    )


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _ulid() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
