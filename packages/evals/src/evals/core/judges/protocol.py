"""Structural contract for field-level judges."""

from typing import Protocol

from evals.core.types import FieldScore


class JudgeProtocol(Protocol):
    """A judge scores `actual` against `expected` per field, emitting a FieldScore."""

    def score(self, *, expected: dict, actual: dict) -> FieldScore: ...
