"""Retrieval eval harness.

Evaluates the end-to-end retrieval system — chunker + embedding model +
vector index + query path — that the ``populate_vector_store`` pipeline
produces. Metrics measure how well a candidate ``(model, dims, chunker)``
configuration retrieves the right document for a given query.

Submodules:

- :mod:`evals.retrieval.types`     — eval-pair dataclass + result types
- :mod:`evals.retrieval.dataset`   — JSONL loader for the eval set
- :mod:`evals.retrieval.cache`     — disk-backed embedding cache (wraps
  :class:`retrievers.embedding.Embedder`)
- :mod:`evals.retrieval.metrics`   — Recall@k / MRR@k / nDCG@k
- :mod:`evals.retrieval.runner`    — index → query → metrics orchestration
- :mod:`evals.retrieval.cli`       — ``eval-retrieval`` console script
"""
