# Shared types for chunking strategies.

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    """A single chunk of content with its heading context."""

    text: str
    heading: str  # nearest heading ancestor (empty if none)
    index: int  # position in the chunk sequence
