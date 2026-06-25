"""Production chat_fn for the specificity judge — structured LLM output, mocked."""

from unittest.mock import patch

from evals.wiki.chat import SpecificityAnalysisModel, make_specificity_chat_fn
from workflows.llm import LLMCall


def test_specificity_chat_fn_returns_dict_and_records_cost():
    parsed = SpecificityAnalysisModel.model_validate(
        {
            "names_orgs": [{"anchor": "Alice", "preserved": True}],
            "quotes": [],
            "abstractions": [],
        }
    )
    call = LLMCall(content="", model="gpt-4.1", input_tokens=50, output_tokens=10)
    calls: list[LLMCall] = []
    chat_fn = make_specificity_chat_fn(model="gpt-4.1", calls_sink=calls)

    with patch("evals.wiki.chat.generate_structured_with_usage", return_value=(parsed, call)) as m:
        out = chat_fn("some judge prompt")

    assert out == {
        "names_orgs": [{"anchor": "Alice", "preserved": True}],
        "quotes": [],
        "abstractions": [],
    }
    assert calls == [call]
    assert m.call_args.kwargs["model"] == "gpt-4.1"
