"""Cosine-similarity scorer with injected embed_fn.

`embed_fn` is `Callable[[str], list[float]]`. Tests pass a dict-lookup mock.
Production wires in `retrievers.embedding.OpenAIEmbedder.embed` (later step).
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from evals.core.types import FieldScore


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class EmbeddingSimilarityJudge:
    fields: Sequence[str]
    embed_fn: Callable[[str], list[float]]

    def score(self, *, expected: dict, actual: dict) -> FieldScore:
        values: dict[str, float] = {}
        for f in self.fields:
            e = expected.get(f)
            a = actual.get(f)
            if not isinstance(e, str) or not isinstance(a, str):
                values[f] = 0.0
                continue
            values[f] = _cosine(self.embed_fn(e), self.embed_fn(a))
        return FieldScore(value=values, metadata={"judge_name": "embedding"})
