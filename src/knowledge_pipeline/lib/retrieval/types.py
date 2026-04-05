# Shared types for retrieval strategies.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalResult:
    """A single retrieval result with score and metadata."""

    chunk_id: str
    content_id: str
    document: str
    score: float  # 0-1, higher is better
    metadata: dict = field(default_factory=dict)
