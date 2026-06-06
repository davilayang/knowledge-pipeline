"""Tests for evals.extraction.scorers — per-Topic-Card-field judge mix."""

import pytest
from evals.extraction.scorers import TopicCardScorer


def _stub_embed(vecs: dict[str, list[float]]):
    """Deterministic embed_fn — returns the vector mapped to the input string."""

    def embed(text: str) -> list[float]:
        return vecs.get(text, [0.0] * 4)

    return embed


def _stub_chat(per_field_scores: dict[str, float]):
    """LLMJudge stub — returns the same per-field score dict regardless of prompt."""

    def chat(prompt: str) -> dict:
        return per_field_scores

    return chat


def test_scorer_uses_exact_match_for_extracted_title():
    scorer = TopicCardScorer(
        embed_fn=_stub_embed({}),
        chat_fn=_stub_chat({"best_example": 1.0, "main_tension": 1.0, "candidate_tie_backs": 1.0}),
    )
    score = scorer.score(
        expected={"extracted_title": "Hello world"},
        actual={"extracted_title": "Hello world"},
    )
    assert score.value["extracted_title"] == 1.0


def test_scorer_exact_mismatch_yields_zero_for_title():
    scorer = TopicCardScorer(
        embed_fn=_stub_embed({}),
        chat_fn=_stub_chat({"best_example": 1.0, "main_tension": 1.0, "candidate_tie_backs": 1.0}),
    )
    score = scorer.score(
        expected={"extracted_title": "Hello world"},
        actual={"extracted_title": "Goodbye world"},
    )
    assert score.value["extracted_title"] == 0.0


def test_scorer_uses_embedding_for_core_mechanism():
    embed_fn = _stub_embed(
        {
            "Mech A": [1.0, 0.0, 0.0, 0.0],
            "Mech A again": [1.0, 0.0, 0.0, 0.0],
        }
    )
    scorer = TopicCardScorer(
        embed_fn=embed_fn,
        chat_fn=_stub_chat({"best_example": 1.0, "main_tension": 1.0, "candidate_tie_backs": 1.0}),
    )
    score = scorer.score(
        expected={"core_mechanism": "Mech A"},
        actual={"core_mechanism": "Mech A again"},
    )
    assert score.value["core_mechanism"] == pytest.approx(1.0, abs=1e-6)


def test_scorer_uses_llm_judge_for_best_example():
    scorer = TopicCardScorer(
        embed_fn=_stub_embed({}),
        chat_fn=_stub_chat({"best_example": 0.83, "main_tension": 0.5, "candidate_tie_backs": 0.5}),
    )
    score = scorer.score(
        expected={"best_example": "Example A"},
        actual={"best_example": "Example B"},
    )
    assert score.value["best_example"] == pytest.approx(0.83, abs=1e-6)


def test_scorer_coerces_list_field_to_string():
    """candidate_tie_backs is a list in production — must be joined before judging."""
    scorer = TopicCardScorer(
        embed_fn=_stub_embed({}),
        chat_fn=_stub_chat({"best_example": 1.0, "main_tension": 1.0, "candidate_tie_backs": 0.7}),
    )
    score = scorer.score(
        expected={"candidate_tie_backs": ["A", "B"]},
        actual={"candidate_tie_backs": ["A", "C"]},
    )
    assert score.value["candidate_tie_backs"] == pytest.approx(0.7, abs=1e-6)


def test_scorer_overall_is_mean_of_field_scores():
    embed_fn = _stub_embed(
        {
            "X": [1.0, 0.0, 0.0, 0.0],
        }
    )
    scorer = TopicCardScorer(
        embed_fn=embed_fn,
        chat_fn=_stub_chat({"best_example": 0.5, "main_tension": 0.5, "candidate_tie_backs": 0.5}),
    )
    expected = {
        "extracted_title": "T",
        "core_mechanism": "X",
        "best_example": "Y",
        "main_tension": "Z",
        "transferable_pattern": "X",
        "candidate_tie_backs": "W",
    }
    actual = dict(expected)
    # title exact 1.0, core embed 1.0, transferable embed 1.0, llm trio 0.5 each → mean = 0.75
    score = scorer.score(expected=expected, actual=actual)
    assert score.value["__overall__"] == pytest.approx(0.75, abs=1e-6)


def test_scorer_metadata_records_judge_per_field():
    scorer = TopicCardScorer(
        embed_fn=_stub_embed({}),
        chat_fn=_stub_chat({"best_example": 1.0, "main_tension": 1.0, "candidate_tie_backs": 1.0}),
    )
    score = scorer.score(
        expected={"extracted_title": "T"},
        actual={"extracted_title": "T"},
    )
    judge_per_field = score.metadata["judge_per_field"]
    assert judge_per_field["extracted_title"] == "exact"
    assert judge_per_field["core_mechanism"] == "embedding"
    assert judge_per_field["best_example"] == "llm"
