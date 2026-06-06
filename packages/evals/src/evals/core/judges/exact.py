"""Exact-equality scorer. 1.0 if `actual[field] == expected[field]`, else 0.0."""

from collections.abc import Sequence
from dataclasses import dataclass

from evals.core.types import FieldScore


@dataclass(frozen=True)
class ExactMatchJudge:
    fields: Sequence[str]

    def score(self, *, expected: dict, actual: dict) -> FieldScore:
        values = {f: 1.0 if expected.get(f) == actual.get(f) else 0.0 for f in self.fields}
        return FieldScore(value=values, metadata={"judge_name": "exact"})
