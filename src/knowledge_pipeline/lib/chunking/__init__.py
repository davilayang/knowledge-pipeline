# Chunking package — pluggable chunking strategies for RAG pipelines.
#
# Re-exports for backward compatibility:
#   from knowledge_pipeline.lib.chunking import Chunk, chunk_markdown

from .markdown import MarkdownChunking, chunk_markdown
from .protocol import ChunkingStrategy
from .types import Chunk

__all__ = ["Chunk", "ChunkingStrategy", "MarkdownChunking", "chunk_markdown"]
