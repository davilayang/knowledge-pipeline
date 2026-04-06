# Chunking package — pluggable chunking strategies for RAG pipelines.

from .registry import get_chunking_fn
from .types import Chunk

__all__ = ["Chunk", "get_chunking_fn"]
