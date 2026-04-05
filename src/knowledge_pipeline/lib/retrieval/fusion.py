# Fusion retrieval — generates multiple query variants via LLM, retrieves for each,
# and fuses results using reciprocal rank fusion.
# Stub: not yet implemented (requires LLM client).

from __future__ import annotations

from typing import Any

from .protocol import RetrievalStrategy
from .types import RetrievalResult


class FusionRetrieval:
    """Generate query variants with an LLM and fuse retrieval results.

    Composition pattern: ``FusionRetrieval(CosineRetrieval(collection), llm_client)``
    """

    def __init__(self, inner: RetrievalStrategy, llm_client: Any = None) -> None:
        self._inner = inner
        self._llm_client = llm_client

    @property
    def name(self) -> str:
        return f"fusion({self._inner.name})"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        raise NotImplementedError(
            "FusionRetrieval is a stub — add LLM client dependency to implement."
        )
