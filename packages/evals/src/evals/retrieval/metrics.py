"""Document-level retrieval metrics for the retrieval eval harness.

Inputs are flat lists of ``content_id``s in rank order (the metadata field
attached to every chunk we upsert into Chroma). A query is evaluated against
its single ``expected_content_id``; multiple chunks of the same document are
all relevant to that query.
"""

import math
from collections.abc import Sequence


def hit_at_k(retrieved_content_ids: Sequence[str], expected: str, k: int) -> int:
    """1 if any chunk of the expected document is in the top-k, else 0.

    Mean across queries == Recall@k at document granularity (binary per query).
    """
    return 1 if expected in retrieved_content_ids[:k] else 0


def mrr_at_k(retrieved_content_ids: Sequence[str], expected: str, k: int) -> float:
    """Reciprocal rank of the first chunk of the expected doc in top-k. 0 if absent."""
    for i, cid in enumerate(retrieved_content_ids[:k], start=1):
        if cid == expected:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved_content_ids: Sequence[str], expected: str, k: int) -> float:
    """Binary-relevance nDCG@k: each chunk of the expected document is relevant.

    IDCG is the max-possible DCG given how many relevant chunks were actually
    retrieved in the top-k — which means a query that retrieved 2 relevant
    chunks scores 1.0 if both are at positions 1 and 2, regardless of how
    many chunks the expected document has on disk.
    """
    rels = [1 if cid == expected else 0 for cid in retrieved_content_ids[:k]]
    n_relevant = sum(rels)
    if n_relevant == 0:
        return 0.0
    dcg = sum(r / math.log2(i + 1) for i, r in enumerate(rels, start=1))
    idcg = sum(1 / math.log2(i + 1) for i in range(1, n_relevant + 1))
    return dcg / idcg


def aggregate_recall(hits: Sequence[int]) -> float:
    if not hits:
        return 0.0
    return sum(hits) / len(hits)


def aggregate_mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
