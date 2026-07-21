"""RunManifest — the one provenance envelope persisted with every eval run.

Replaces per-CLI scattered provenance (local JSON, CLI prints, hand-typed
Notion rows). Frozen + JSON-safe so it rides in RunRecord.config via asdict.
Cross-family-judge is a DERIVED property (judge_is_cross_family), not a stored
field — one source of truth for subject/judge models, no drift.
"""

import subprocess
from dataclasses import dataclass
from typing import Literal


def code_rev() -> str:
    """Git short SHA for the manifest's `code_rev`; 'unknown' outside a checkout."""
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class RunManifest:
    dataset: str  # gold filename
    dataset_schema: int  # fixture header schema_version
    subject: str  # prompt label / variant name under test
    subject_model: str  # extractor / generator model
    judge_model: str | None  # None for deterministic-only surfaces
    code_rev: str  # git short sha
    mode: Literal["gate", "report"]  # gate asserts a committed floor; report only emits
    runs: int  # N for noisy-judge averaging


def format_manifest_line(m: RunManifest) -> str:
    """One-line provenance summary for print-mode harnesses (claims, coverage)."""
    judge = m.judge_model or "none"
    return (
        f"[manifest] dataset={m.dataset} (schema v{m.dataset_schema}) "
        f"subject={m.subject} model={m.subject_model} judge={judge} "
        f"mode={m.mode} runs={m.runs} rev={m.code_rev}"
    )
