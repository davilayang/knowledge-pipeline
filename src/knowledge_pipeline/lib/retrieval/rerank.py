# Reranking retrieval — wraps an inner strategy and reranks results with a cross-encoder.

from __future__ import annotations

from sentence_transformers import CrossEncoder

from .protocol import RetrievalStrategy
from .types import RetrievalResult


class RerankRetrieval:
    """Rerank results from an inner retrieval strategy using a cross-encoder.

    Composition pattern: ``RerankRetrieval(CosineRetrieval(collection))``

    The inner strategy retrieves a broader candidate set (``candidate_factor * n_results``),
    then the cross-encoder scores each (query, document) pair and returns the top ``n_results``.
    """

    def __init__(
        self,
        inner: RetrievalStrategy,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        candidate_factor: int = 4,
    ) -> None:
        self._inner = inner
        self._model_name = model_name
        self._candidate_factor = candidate_factor
        self._model: CrossEncoder | None = None

    def _get_model(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(self._model_name)
        return self._model

    @property
    def name(self) -> str:
        return f"rerank({self._inner.name})"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        n_candidates = n_results * self._candidate_factor
        candidates = self._inner.retrieve(query, n_results=n_candidates)

        if not candidates:
            return []

        model = self._get_model()
        pairs = [[query, c.document] for c in candidates]
        scores = model.predict(pairs)

        for candidate, score in zip(candidates, scores):
            candidate.score = float(score)

        candidates.sort(key=lambda r: r.score, reverse=True)
        return candidates[:n_results]
