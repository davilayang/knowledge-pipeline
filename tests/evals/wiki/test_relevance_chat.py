"""Production chat_fn for the relevance judge — wraps structured LLM output.

The LLM is mocked at the import location (`evals.wiki.chat`), so no real call runs.
"""

from unittest.mock import patch

from evals.wiki.chat import RelevancePassagesModel, make_relevance_chat_fn
from workflows.llm import LLMCall


def test_chat_fn_returns_passages_dict_and_records_cost():
    parsed = RelevancePassagesModel.model_validate(
        {"passages": [{"text": "X", "on_topic": True, "subject": None}]}
    )
    call = LLMCall(content="", model="gpt-4.1", input_tokens=100, output_tokens=20)
    calls: list[LLMCall] = []
    chat_fn = make_relevance_chat_fn(model="gpt-4.1", calls_sink=calls)

    with patch("evals.wiki.chat.generate_structured_with_usage", return_value=(parsed, call)) as m:
        out = chat_fn("some judge prompt")

    # shape the RelevanceJudge consumes
    assert out == {"passages": [{"text": "X", "on_topic": True, "subject": None}]}
    # cost captured for the benchmark to persist
    assert calls == [call]
    # judge model threaded through
    assert m.call_args.kwargs["model"] == "gpt-4.1"
