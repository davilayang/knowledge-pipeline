# Re-export all assets for this strategy.

from knowledge_pipeline.defs.shared.raw_store import raw_store_copy

from .chunking import baseline_chunked
from .embedding import baseline_embedded
from .indexing import baseline_indexed

__all__ = ["raw_store_copy", "baseline_chunked", "baseline_embedded", "baseline_indexed"]
