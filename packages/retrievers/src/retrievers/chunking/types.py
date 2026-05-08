# Re-export Chunk from domains.types so retrievers chunking strategies
# share one canonical type with domain-side chunkers (e.g. turn_grouping).

from domains.types import Chunk

__all__ = ["Chunk"]
