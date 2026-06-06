"""Extraction-pipeline fixture + diff types.

Scope-extension of evals.core: ExtractionFixture pairs raw content with a
v5 baseline expected_topic_card (a regression anchor, not human-curated gold —
see ai-plannings/2026-06-06_evals-workbench-step3-implementation.md).
ExtractionDiffReport is the per-Topic-Card-field side-by-side renderer for
two variant runs.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractionFixture:
    fixture_id: str
    content_type: str
    content: str
    expected_topic_card: dict[str, Any]


@dataclass(frozen=True)
class TopicCardFields:
    @staticmethod
    def canonical() -> tuple[str, ...]:
        return (
            "extracted_title",
            "core_mechanism",
            "best_example",
            "main_tension",
            "transferable_pattern",
            "candidate_tie_backs",
        )


@dataclass(frozen=True)
class ExtractionDiffReport:
    variant_a: str
    variant_b: str
    per_field: dict[str, dict[str, Any]]
    per_field_scores: dict[str, float]
