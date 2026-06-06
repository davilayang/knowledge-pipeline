"""ExactMatchJudge: per-field equality → 1.0 or 0.0."""

from evals.core.judges import ExactMatchJudge


def test_exact_match_all_correct():
    judge = ExactMatchJudge(fields=("title", "mechanism"))
    score = judge.score(
        expected={"title": "X", "mechanism": "Y"},
        actual={"title": "X", "mechanism": "Y"},
    )
    assert score.value == {"title": 1.0, "mechanism": 1.0}


def test_exact_match_partial():
    judge = ExactMatchJudge(fields=("title", "mechanism"))
    score = judge.score(
        expected={"title": "X", "mechanism": "Y"},
        actual={"title": "X", "mechanism": "Z"},
    )
    assert score.value == {"title": 1.0, "mechanism": 0.0}


def test_exact_match_missing_field_is_zero():
    judge = ExactMatchJudge(fields=("title", "mechanism"))
    score = judge.score(expected={"title": "X", "mechanism": "Y"}, actual={"title": "X"})
    assert score.value["mechanism"] == 0.0


def test_judge_metadata_contains_judge_name():
    judge = ExactMatchJudge(fields=("title",))
    score = judge.score(expected={"title": "X"}, actual={"title": "X"})
    assert score.metadata["judge_name"] == "exact"
