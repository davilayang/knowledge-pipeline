"""LLMJudge with mock chat_fn."""

from evals.core.judges import LLMJudge


def _mock_chat(prompt: str) -> dict:
    """Pretend the judge LLM returned per-field scores as JSON."""
    return {"title": 0.9, "mechanism": 0.7}


def test_llm_judge_uses_chat_fn_output():
    judge = LLMJudge(
        fields=("title", "mechanism"),
        chat_fn=_mock_chat,
        prompt_template="Score {expected} vs {actual} on {fields}",
    )
    score = judge.score(
        expected={"title": "X", "mechanism": "Y"},
        actual={"title": "X'", "mechanism": "Y'"},
    )
    assert score.value == {"title": 0.9, "mechanism": 0.7}


def test_metadata_records_raw_chat_output_and_judge_name():
    judge = LLMJudge(
        fields=("title",),
        chat_fn=_mock_chat,
        prompt_template="Score {expected} vs {actual} on {fields}",
    )
    score = judge.score(expected={"title": "X"}, actual={"title": "X'"})
    assert score.metadata["judge_name"] == "llm"
    assert "raw" in score.metadata


def test_missing_field_in_chat_output_scores_zero():
    """If the LLM omits a field, default to 0.0 — caller flags via metadata."""

    def _partial_chat(_prompt: str) -> dict:
        return {"title": 0.9}  # mechanism missing

    judge = LLMJudge(
        fields=("title", "mechanism"),
        chat_fn=_partial_chat,
        prompt_template="...",
    )
    score = judge.score(expected={"title": "X", "mechanism": "Y"}, actual={"title": "X", "mechanism": "Y"})
    assert score.value == {"title": 0.9, "mechanism": 0.0}
