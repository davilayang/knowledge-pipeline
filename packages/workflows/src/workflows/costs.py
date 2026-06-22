# Token-usage → USD pricing for LLM calls.
#
# Per-1M-token rates in USD. Sourced from https://openai.com/api/pricing/
# on 2026-05-08. Update the dict when the prices page changes; historical
# materializations keep their point-in-time numbers (metadata is immutable
# once attached to a materialization).

PRICING_PER_1M: dict[str, dict[str, float]] = {
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
}


def _rate_for(model: str) -> dict[str, float] | None:
    """Per-1M rates for a model id. Falls back to a longest-prefix match so the
    API's dated ids (gpt-4.1-nano-2025-04-14) price at their base alias's rate
    instead of $0."""
    rate = PRICING_PER_1M.get(model)
    if rate is not None:
        return rate
    matches = [(key, r) for key, r in PRICING_PER_1M.items() if model.startswith(key)]
    if not matches:
        return None
    return max(matches, key=lambda kr: len(kr[0]))[1]


def is_priced(model: str) -> bool:
    """True if `model` resolves to a known rate (exact alias or dated id)."""
    return _rate_for(model) is not None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost for one LLM call. Unknown model → 0.0 (don't crash; the
    caller surfaces the unknown-model name separately so the gap is visible)."""
    rate = _rate_for(model)
    if rate is None:
        return 0.0
    return (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000
