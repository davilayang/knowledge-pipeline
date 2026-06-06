"""CostBudget + estimator protocol."""

import pytest
from evals.core.cost import (
    BudgetExceededError,
    CostBudget,
    CostEstimatorProtocol,
)


def test_default_budget_values():
    b = CostBudget()
    assert b.max_concurrent_calls == 4
    assert b.max_cost_usd_per_run == 5.00


def test_budget_is_frozen():
    b = CostBudget()
    with pytest.raises(Exception):
        b.max_concurrent_calls = 8  # type: ignore[misc]


def test_check_estimate_within_budget_passes():
    b = CostBudget(max_cost_usd_per_run=10.0)
    b.check_estimate(estimated_usd=2.50)  # no raise


def test_check_estimate_over_budget_raises():
    b = CostBudget(max_cost_usd_per_run=1.0)
    with pytest.raises(BudgetExceededError) as exc:
        b.check_estimate(estimated_usd=2.50)
    assert "2.50" in str(exc.value)
    assert "1.00" in str(exc.value)


def test_estimator_protocol_structural_match():
    """Concrete estimator just needs to be callable returning a float."""

    class _Estimator:
        def __call__(self, *, fixtures: int, tokens_per_call: int, calls_per_fixture: int) -> float:
            return fixtures * calls_per_fixture * tokens_per_call * 1e-6

    e: CostEstimatorProtocol = _Estimator()
    assert e(fixtures=10, tokens_per_call=1000, calls_per_fixture=3) == 0.03
