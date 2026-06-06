"""Frozen dataclass records used across the eval substrate.

Every type here is JSON-serializable (via dataclasses.asdict) — that's the
load-bearing contract that lets RunRecord persist cleanly without a custom
encoder. Non-serializable runtime objects (DB connections, LangGraph state
fields) live ONLY behind the snapshotter sentinel pattern; they never enter
these records directly.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class RunStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class VariantProvenance:
    prompt_versions: Mapping[str, str]
    model_versions: Mapping[str, str]
    code_revision: str
    corpus_anchor: str | None
    output_schema_version: int


@dataclass(frozen=True)
class StageTrace:
    node: str
    input_snapshot: dict
    output_snapshot: dict
    tokens_in: int
    tokens_out: int
    duration_ms: int


@dataclass(frozen=True)
class FixtureRun:
    fixture_id: str
    status: RunStatus
    output: dict | None
    stages: list[StageTrace]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    error_message: str | None = None


@dataclass(frozen=True)
class FixtureRef:
    fixture_id: str
    fixture_set: str
    schema_version: int


@dataclass(frozen=True)
class FieldScore:
    """Per-field score output. Mirrors Inspect AI's `Score(value=dict, metadata=...)`
    shape so a future `inspect view` adapter is cheap."""

    value: Mapping[str, float]
    metadata: dict


@dataclass(frozen=True)
class ScoreReport:
    scorer_name: str
    metrics: Mapping[str, float]
    stratifications: Mapping[str, Mapping[str, float]]
    sample_count: int


@dataclass(frozen=True)
class RunRecord:
    """One variant run over one fixture set. Persisted as run.json per run dir."""

    run_id: str
    kind: Literal["workbench", "benchmark"]
    target: str
    variant_name: str
    variant_config: dict
    variant_provenance: VariantProvenance
    fixture_set: str
    fixture_anchor: str | None
    started_at: str
    completed_at: str
    samples: list[FixtureRun]
    scores: list[ScoreReport]
    config: dict
