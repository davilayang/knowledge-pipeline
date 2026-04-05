# Reranking retrieval — wraps an inner strategy and reranks results.
# Stub: not yet implemented (requires a cross-encoder model).

from __future__ import annotations

from .protocol import RetrievalStrategy
from .types import RetrievalResult


class RerankRetrieval:
    """Rerank results from an inner retrieval strategy using a cross-encoder.

    Composition pattern: ``RerankRetrieval(CosineRetrieval(collection))``
    """

    def __init__(
        self,
        inner: RetrievalStrategy,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ) -> None:
        self._inner = inner
        self._model_name = model_name

    @property
    def name(self) -> str:
        return f"rerank({self._inner.name})"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        raise NotImplementedError(
            "RerankRetrieval is a stub — add cross-encoder dependency to implement."
        )
