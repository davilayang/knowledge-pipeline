# Lost-in-the-middle reordering.
#
# LLMs attend more to the beginning and end of their context window.
# This reorders results so the best-scored items appear at the edges
# (positions 0, N-1, 1, N-2, ...) rather than strictly by rank.

from __future__ import annotations

from knowledge_pipeline.lib.retrieval.types import RetrievalResult


class LostInMiddleReorder:
    """Reorder results so highest-scored items are at the edges of the list.

    Given results ranked [1, 2, 3, 4, 5] by score, produces:
    [1, 3, 5, 4, 2] — best at start, second-best at end, weaker in middle.
    """

    @property
    def name(self) -> str:
        return "lost_in_middle_reorder"

    def process(self, results: list[RetrievalResult], query: str) -> list[RetrievalResult]:
        if len(results) <= 2:
            return list(results)

        # Assume results are already sorted by score descending.
        left: list[RetrievalResult] = []
        right: list[RetrievalResult] = []

        for i, result in enumerate(results):
            if i % 2 == 0:
                left.append(result)
            else:
                right.append(result)

        # left gets even indices (0, 2, 4, ...) — strongest first
        # right gets odd indices (1, 3, 5, ...) — reversed so second-best is at end
        return left + list(reversed(right))
