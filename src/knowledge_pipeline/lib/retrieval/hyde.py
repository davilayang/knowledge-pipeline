# HyDE retrieval — Hypothetical Document Embeddings.
# Generates a hypothetical answer via LLM, embeds it, and retrieves similar chunks.
# Stub: not yet implemented (requires LLM client).

from __future__ import annotations

from typing import Any

from .protocol import RetrievalStrategy
from .types import RetrievalResult


class HyDERetrieval:
    """Hypothetical Document Embeddings retrieval.

    Uses an LLM to generate a hypothetical answer, embeds it, and retrieves
    chunks similar to the hypothetical answer rather than the original query.

    Composition pattern: ``HyDERetrieval(CosineRetrieval(collection), llm_client)``
    """

    def __init__(self, inner: RetrievalStrategy, llm_client: Any = None) -> None:
        self._inner = inner
        self._llm_client = llm_client

    @property
    def name(self) -> str:
        return f"hyde({self._inner.name})"

    def retrieve(self, query: str, n_results: int = 5) -> list[RetrievalResult]:
        raise NotImplementedError(
            "HyDERetrieval is a stub — add LLM client dependency to implement."
        )
