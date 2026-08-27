"""Pinned extract-claims eval cohort — loader over the checked-in JSONL.

The cohort (`packages/evals/datasets/extract_claims_eval.jsonl`) is the
load-bearing pinned artifact: a fixed set of real sources (2 per content shape)
scored run-over-run so faithfulness / tagging / stability trends are comparable.
The JSONL is the checked-in artifact; this module reads it.
"""

from dataclasses import dataclass
from pathlib import Path

from evals.core.fixtures import load_fixtures

# packages/evals/datasets/extract_claims_eval.jsonl, resolved from this module:
# dataset.py → extract_claims → wiki → evals → src → evals(pkg) → datasets/.
DATASET_PATH = Path(__file__).resolve().parents[4] / "datasets" / "extract_claims_eval.jsonl"

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SourceFixture:
    """One pinned source: the real fetched/transcribed body the source writer produces
    claims from. `content_type` gates the transcript prime; `content_shape` is the genre
    label, kept as the per-genre reporting stratification."""

    id: str
    content_shape: str
    content_type: str
    title: str
    content_date: str | None
    body: str


def load_source_fixtures(path: Path = DATASET_PATH) -> list[SourceFixture]:
    """Load the pinned cohort. Schema-versioned; a bad header raises loudly."""
    _, rows = load_fixtures(path, expected_schema_version=SCHEMA_VERSION)
    return [
        SourceFixture(
            id=r["id"],
            content_shape=r["content_shape"],
            content_type=r["content_type"],
            title=r["title"],
            content_date=r.get("content_date"),
            body=r["body"],
        )
        for r in rows
    ]
