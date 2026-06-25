"""Production chat_fn for the faithfulness judge — wraps structured LLM output.

The LLM is mocked at the import location (`evals.wiki.chat`), so no real call runs.
"""

from unittest.mock import patch

from evals.wiki.chat import FaithfulnessClaimsModel, make_faithfulness_chat_fn
from workflows.llm import LLMCall


def test_chat_fn_returns_claims_dict_and_records_cost():
    parsed = FaithfulnessClaimsModel.model_validate(
        {"claims": [{"text": "X", "supported": True, "evidence": None}]}
    )
    call = LLMCall(content="", model="gpt-4.1", input_tokens=100, output_tokens=20)
    calls: list[LLMCall] = []
    chat_fn = make_faithfulness_chat_fn(model="gpt-4.1", calls_sink=calls)

    with patch("evals.wiki.chat.generate_structured_with_usage", return_value=(parsed, call)) as m:
        out = chat_fn("some judge prompt")

    # shape the FaithfulnessJudge consumes
    assert out == {"claims": [{"text": "X", "supported": True, "evidence": None}]}
    # cost captured for the benchmark to persist
    assert calls == [call]
    # judge model threaded through
    assert m.call_args.kwargs["model"] == "gpt-4.1"
