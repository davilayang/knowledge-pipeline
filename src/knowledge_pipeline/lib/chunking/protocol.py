# Protocol for pluggable chunking strategies.

from __future__ import annotations

from typing import Protocol

from .types import Chunk


class ChunkingStrategy(Protocol):
    """Interface for a chunking strategy."""

    @property
    def name(self) -> str:
        """Human-readable name for this strategy."""
        ...

    def chunk(self, text: str) -> list[Chunk]:
        """Split text into chunks."""
        ...
