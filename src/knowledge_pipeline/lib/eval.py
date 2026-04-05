# Retrieval evaluation metrics for comparing RAG strategies.

from __future__ import annotations


def recall_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int = 5) -> float:
    """Fraction of expected IDs found in the top-k retrieved results."""
    if not expected_ids:
        return 0.0
    top_k = set(retrieved_ids[:k])
    hits = sum(1 for eid in expected_ids if eid in top_k)
    return hits / len(expected_ids)


def precision_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int = 5) -> float:
    """Fraction of top-k retrieved results that are in the expected set."""
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    expected_set = set(expected_ids)
    hits = sum(1 for rid in top_k if rid in expected_set)
    return hits / len(top_k)


def mrr(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """Mean Reciprocal Rank — 1/rank of the first relevant result."""
    expected_set = set(expected_ids)
    for i, rid in enumerate(retrieved_ids):
        if rid in expected_set:
            return 1.0 / (i + 1)
    return 0.0
