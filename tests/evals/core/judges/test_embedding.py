"""EmbeddingSimilarityJudge with mock embed_fn."""

from evals.core.judges import EmbeddingSimilarityJudge


def _mock_embed(text: str) -> list[float]:
    return {"hello": [1.0, 0.0], "hi": [0.99, 0.01], "bye": [0.0, 1.0]}[text]


def test_high_similarity_close_to_one():
    judge = EmbeddingSimilarityJudge(fields=("greeting",), embed_fn=_mock_embed)
    score = judge.score(expected={"greeting": "hello"}, actual={"greeting": "hi"})
    assert score.value["greeting"] > 0.9


def test_low_similarity_close_to_zero():
    judge = EmbeddingSimilarityJudge(fields=("greeting",), embed_fn=_mock_embed)
    score = judge.score(expected={"greeting": "hello"}, actual={"greeting": "bye"})
    assert score.value["greeting"] < 0.1


def test_missing_field_scores_zero():
    judge = EmbeddingSimilarityJudge(fields=("greeting",), embed_fn=_mock_embed)
    score = judge.score(expected={"greeting": "hello"}, actual={})
    assert score.value["greeting"] == 0.0


def test_metadata_has_judge_name():
    judge = EmbeddingSimilarityJudge(fields=("greeting",), embed_fn=_mock_embed)
    score = judge.score(expected={"greeting": "hello"}, actual={"greeting": "hi"})
    assert score.metadata["judge_name"] == "embedding"
