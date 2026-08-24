# Token-usage → USD pricing for LLM calls.
#
# Per-1M text-token rates, Standard tier (Batch and Flex are cheaper, not
# modelled). Source: https://developers.openai.com/api/docs/pricing — every
# rate below re-verified 2026-08-24. Historical materializations keep their
# point-in-time numbers, so updating here never rewrites past metadata.
#
# A missing model resolves to $0.00 rather than raising, so a gap is silent.
# `gpt-4.1` is the one that matters today: `evals.wiki.calibrate` is the only
# caller of `cost_usd` anywhere, and it prices the wiki judge.
#
# Matching is exact-then-longest-PREFIX, so a key silently covers every id that
# extends it — `gpt-4o-mini` would price `gpt-4o-mini-tts` at the text rate.
# Only add a key whose whole subtree shares its rate.
#
# Input uses the UNCACHED rate. Cached is 4x cheaper on gpt-4.1, 10x on
# gpt-5-mini and luna, so no single blended discount is right; this overstates.

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
