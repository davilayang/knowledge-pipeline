"""RunManifest — the one provenance envelope recorded with every eval run.

Gives every eval a single "what dataset / model / judge / code / runs produced
this score" answer, instead of provenance scattered across local JSON, CLI
prints, and hand-typed Notion rows. Frozen + JSON-safe so it rides in
RunRecord.config via asdict. It stores `subject_model` and `judge_model`
separately; whether the judge is a different family is read off those two, not
stored as a third field that could drift out of sync.
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
    # Revision of the DATA, distinct from the header's schema_version above:
    # adding fixtures changes the scoring population without changing the
    # schema, so a score is only traceable to the rows that produced it if
    # both are recorded. Defaulted so harnesses that don't version data are
    # unaffected.
    dataset_version: int = 1


def format_manifest_line(m: RunManifest) -> str:
    """One-line provenance summary for print-mode harnesses (claims, coverage)."""
    judge = m.judge_model or "none"
    return (
        f"[manifest] dataset={m.dataset} (schema v{m.dataset_schema}) "
        f"subject={m.subject} model={m.subject_model} judge={judge} "
        f"mode={m.mode} runs={m.runs} rev={m.code_rev}"
    )
