# Retrieval package — pluggable retrieval strategies for RAG pipelines.

from .cosine import CosineRetrieval
from .protocol import RetrievalStrategy
from .registry import build_strategy
from .types import RetrievalResult

__all__ = ["CosineRetrieval", "RetrievalResult", "RetrievalStrategy", "build_strategy"]
