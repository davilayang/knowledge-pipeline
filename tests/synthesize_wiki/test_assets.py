"""Pure-function tests for synthesize_wiki/assets.py helpers.

Asset functions themselves require wiki_pg + raw_store.db + LangGraph
mocking (covered by the wiki_synthesis test suite). These cover the
pure helpers — _cost_metadata aggregation correctness — where a
regression would silently change a Dagster materialization's metadata.
"""

from orchestrators.defs.pipelines.synthesize_wiki.assets import _cost_metadata
from workflows.llm import LLMCall


def _call(model: str, input_tokens: int, output_tokens: int) -> LLMCall:
    return LLMCall(content="", model=model, input_tokens=input_tokens, output_tokens=output_tokens)


def test_cost_metadata_aggregates_by_model():
    calls = [
        _call("gpt-4.1-mini", 1_000, 500),
        _call("gpt-4.1-mini", 2_000, 1_000),
        _call("gpt-4.1-nano", 500, 200),
    ]
    md = _cost_metadata(calls)

    assert md["llm_calls"].value == 3
    assert md["input_tokens"].value == 3_500
    assert md["output_tokens"].value == 1_700
    by_model = md["cost_by_model"].value
    assert set(by_model.keys()) == {"gpt-4.1-mini", "gpt-4.1-nano"}
    assert by_model["gpt-4.1-mini"]["calls"] == 2
    assert by_model["gpt-4.1-mini"]["input_tokens"] == 3_000
    assert by_model["gpt-4.1-nano"]["calls"] == 1


def test_cost_metadata_surfaces_unknown_model():
    """Unknown model: zero cost contribution + name surfaced in unknown_pricing_models."""
    calls = [
        _call("gpt-4.1-mini", 1_000, 0),
        _call("gpt-99-fake", 1_000_000, 1_000_000),
    ]
    md = _cost_metadata(calls)
    assert "unknown_pricing_models" in md
    assert md["unknown_pricing_models"].value == ["gpt-99-fake"]
    # The unknown model's million tokens contribute 0; cost is from gpt-4.1-mini only.
    assert md["cost_usd"].value == round(1_000 * 0.40 / 1_000_000, 4)


def test_cost_metadata_omits_unknown_key_when_all_known():
    """Don't emit unknown_pricing_models when there's nothing to flag."""
    md = _cost_metadata([_call("gpt-4.1-mini", 100, 100)])
    assert "unknown_pricing_models" not in md


def test_cost_metadata_empty_calls():
    """Zero-call case (no items processed) returns zeros, not crashes."""
    md = _cost_metadata([])
    assert md["llm_calls"].value == 0
    assert md["input_tokens"].value == 0
    assert md["cost_usd"].value == 0.0
    assert md["cost_by_model"].value == {}
