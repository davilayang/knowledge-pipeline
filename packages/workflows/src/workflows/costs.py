# Token-usage → USD pricing for LLM calls.
#
# Per-1M-token rates in USD. Sourced from https://developers.openai.com/api/docs/pricing
# on 2026-08-23. Update the dict when the prices page changes; historical
# materializations keep their point-in-time numbers (metadata is immutable
# once attached to a materialization).
#
# A model missing here costs $0.00 rather than raising, so a gap is silent.
# `gpt-4.1` is the one that matters today: it is the wiki judge, and
# `evals.wiki.calibrate` is the only caller of `cost_usd` anywhere, so it is the
# only model whose cost is ever displayed. The rest are listed because the
# pipeline is pointed at them, not because anything costs them yet.
#
# Matching is exact-then-longest-PREFIX, so a key only covers ids that EXTEND
# it, and a key with siblings on different economics is a trap: `gpt-4o-mini`
# would silently price `gpt-4o-mini-tts` and `-transcribe` at the text rate.
# Add a key only when its whole prefix subtree shares its rate.
#
# Input is priced at the uncached rate. Cached input is ~10x cheaper and the
# call records carry `cached_tokens`, so a cache-aware rate would be a real
# improvement — deliberately not done here, and it overstates rather than
# understates cost.

PRICING_PER_1M: dict[str, dict[str, float]] = {
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
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
