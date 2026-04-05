# Protocol for post-retrieval processing steps.

from __future__ import annotations

from typing import Protocol

from knowledge_pipeline.lib.retrieval.types import RetrievalResult


class PostProcessor(Protocol):
    """Interface for a post-retrieval processor."""

    @property
    def name(self) -> str:
        """Human-readable name for this processor."""
        ...

    def process(self, results: list[RetrievalResult], query: str) -> list[RetrievalResult]:
        """Transform retrieval results (reorder, deduplicate, etc.)."""
        ...
