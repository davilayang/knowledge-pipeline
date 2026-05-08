"""Tests for workflows.costs.cost_usd.

Foundation that _cost_metadata's math depends on; trivial but pins the
arithmetic and the unknown-model contract against a future pricing-table
edit silently breaking the formula.
"""

from workflows.costs import PRICING_PER_1M, cost_usd


def test_cost_usd_known_model_input_only():
    rate = PRICING_PER_1M["gpt-4.1-mini"]["input"]
    assert cost_usd("gpt-4.1-mini", input_tokens=1_000_000, output_tokens=0) == rate


def test_cost_usd_known_model_output_only():
    rate = PRICING_PER_1M["gpt-4.1-mini"]["output"]
    assert cost_usd("gpt-4.1-mini", input_tokens=0, output_tokens=1_000_000) == rate


def test_cost_usd_combined():
    """Weighted sum across input + output rates."""
    expected = (1000 * 0.10 + 500 * 0.40) / 1_000_000
    assert cost_usd("gpt-4.1-nano", 1000, 500) == expected


def test_cost_usd_unknown_model_returns_zero():
    """Unknown model is not a hard error — zero cost, caller surfaces the gap."""
    assert cost_usd("gpt-99-fake", 1_000_000, 1_000_000) == 0.0


def test_cost_usd_zero_tokens_zero_cost():
    assert cost_usd("gpt-4.1-mini", 0, 0) == 0.0
