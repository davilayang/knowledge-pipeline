# Protocol for post-retrieval processing steps.

from typing import Protocol

from retrievers.retrieval.types import RetrievalResult


class PostProcessor(Protocol):
    """Interface for a post-retrieval processor."""

    @property
    def name(self) -> str:
        """Human-readable name for this processor."""
        ...

    def process(self, results: list[RetrievalResult], query: str) -> list[RetrievalResult]:
        """Transform retrieval results (reorder, deduplicate, etc.)."""
        ...
