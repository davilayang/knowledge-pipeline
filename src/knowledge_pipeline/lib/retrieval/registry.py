# Factory for building retrieval strategies from spec strings.

from __future__ import annotations

import chromadb

from .cosine import CosineRetrieval
from .protocol import RetrievalStrategy

# Map of strategy spec names to builder functions.
_STRATEGY_BUILDERS: dict[str, type] = {
    "cosine": CosineRetrieval,
}


def build_strategy(
    collection: chromadb.Collection,
    strategy_spec: str = "cosine",
) -> RetrievalStrategy:
    """Build a retrieval strategy from a spec string.

    Args:
        collection: ChromaDB collection to query.
        strategy_spec: Name of the retrieval strategy (e.g. "cosine").

    Returns:
        An instance implementing ``RetrievalStrategy``.

    Raises:
        ValueError: If the strategy spec is not recognized.
    """
    builder = _STRATEGY_BUILDERS.get(strategy_spec)
    if builder is None:
        available = ", ".join(sorted(_STRATEGY_BUILDERS))
        raise ValueError(f"Unknown retrieval strategy: {strategy_spec!r}. Available: {available}")
    return builder(collection)  # type: ignore[return-value]
