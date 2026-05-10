import math

import pytest
from evals.retrieval.metrics import (
    aggregate_mean,
    aggregate_recall,
    hit_at_k,
    mrr_at_k,
    ndcg_at_k,
)


class TestHitAtK:
    def test_match_in_top_k(self):
        assert hit_at_k(["a", "b", "X", "c"], "X", 5) == 1

    def test_match_outside_top_k(self):
        assert hit_at_k(["a", "b", "c", "d", "e", "X"], "X", 5) == 0

    def test_no_match(self):
        assert hit_at_k(["a", "b", "c"], "X", 5) == 0

    def test_k_zero(self):
        assert hit_at_k(["X"], "X", 0) == 0


class TestMrrAtK:
    def test_first_position(self):
        assert mrr_at_k(["X", "a", "b"], "X", 10) == 1.0

    def test_third_position(self):
        assert mrr_at_k(["a", "b", "X"], "X", 10) == pytest.approx(1 / 3)

    def test_outside_k(self):
        assert mrr_at_k(["a", "b", "c", "d", "X"], "X", 3) == 0.0

    def test_no_match(self):
        assert mrr_at_k(["a", "b"], "X", 10) == 0.0

    def test_only_first_match_counts(self):
        # Multiple chunks of expected doc — earliest rank wins.
        assert mrr_at_k(["a", "X", "X"], "X", 10) == pytest.approx(1 / 2)

    def test_k_zero(self):
        assert mrr_at_k(["X"], "X", 0) == 0.0


class TestNdcgAtK:
    def test_no_match_is_zero(self):
        assert ndcg_at_k(["a", "b", "c"], "X", 10) == 0.0

    def test_perfect_ranking(self):
        # All relevant chunks at the top → DCG == IDCG → 1.0.
        assert ndcg_at_k(["X", "X", "X", "a", "b"], "X", 10) == pytest.approx(1.0)

    def test_partial_ordering_below_perfect(self):
        # One relevant at rank 3 vs ideal at rank 1.
        score = ndcg_at_k(["a", "b", "X", "c"], "X", 10)
        assert 0.0 < score < 1.0
        # DCG = 1/log2(4) = 0.5; IDCG = 1/log2(2) = 1 → 0.5
        assert score == pytest.approx(1 / math.log2(4))

    def test_relevant_outside_k_excluded(self):
        # X at rank 11 with k=10 → not retrieved → 0.
        retrieved = ["a"] * 10 + ["X"]
        assert ndcg_at_k(retrieved, "X", 10) == 0.0

    def test_k_zero(self):
        assert ndcg_at_k(["X", "X"], "X", 0) == 0.0

    def test_k_larger_than_retrieved(self):
        # Chroma may return fewer than k chunks for small collections; the
        # metric must handle the short list without padding/erroring.
        assert ndcg_at_k(["X", "a"], "X", 100) == pytest.approx(1.0)


class TestAggregations:
    def test_recall_mean(self):
        assert aggregate_recall([1, 0, 1, 1]) == 0.75

    def test_recall_empty(self):
        assert aggregate_recall([]) == 0.0

    def test_mean(self):
        assert aggregate_mean([0.5, 1.0, 0.0]) == pytest.approx(0.5)

    def test_mean_empty(self):
        assert aggregate_mean([]) == 0.0
