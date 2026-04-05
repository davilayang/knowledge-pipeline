# Protocol for pluggable retrieval strategies.

from __future__ import annotations

from typing import Protocol

from .types import RetrievalResult


class RetrievalStrategy(Protocol):
    """Interface for a retrieval strategy."""

    @property
    def name(self) -> str:
        """Human-readable name for this strategy."""
        ...

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        """Retrieve relevant chunks for a query."""
        ...
