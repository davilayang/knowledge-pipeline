# Factory for building retrieval strategies from spec strings.

from __future__ import annotations

from collections.abc import Callable

import chromadb

from .cosine import CosineRetrieval
from .protocol import RetrievalStrategy
from .rerank import RerankRetrieval

# Map of strategy spec names to builder callables.
# Each builder takes a ChromaDB collection and returns a RetrievalStrategy.
_STRATEGY_BUILDERS: dict[str, Callable[[chromadb.Collection], RetrievalStrategy]] = {
    "cosine": lambda col: CosineRetrieval(col),
    "rerank": lambda col: RerankRetrieval(CosineRetrieval(col)),
}


def build_strategy(
    collection: chromadb.Collection,
    strategy_spec: str = "cosine",
) -> RetrievalStrategy:
    """Build a retrieval strategy from a spec string.

    Args:
        collection: ChromaDB collection to query.
        strategy_spec: Name of the retrieval strategy (e.g. "cosine", "rerank").

    Returns:
        An instance implementing ``RetrievalStrategy``.

    Raises:
        ValueError: If the strategy spec is not recognized.
    """
    builder = _STRATEGY_BUILDERS.get(strategy_spec)
    if builder is None:
        available = ", ".join(sorted(_STRATEGY_BUILDERS))
        raise ValueError(f"Unknown retrieval strategy: {strategy_spec!r}. Available: {available}")
    return builder(collection)
