"""RelevanceJudge — does the page stay ABOUT {entity}, or drift into other subjects?

The axis faithfulness + specificity miss: a page can be fully grounded and keep
every specific yet still drift into a tangential subject (the YOYO-pollution case).
The judge LLM is injected as `chat_fn`; tests pass a stub passage analysis.
"""

import pytest
from evals.wiki.judges import RelevanceJudge


def _stub_three_passages(_prompt: str) -> dict:
    """Pretend the judge LLM split the page into 3 passages, 1 a drift."""
    return {
        "passages": [
            {"text": "Anthropic was founded in 2021.", "on_topic": True, "subject": None},
            {"text": "It builds the Claude family of models.", "on_topic": True, "subject": None},
            {
                "text": "Separately, the YOYO experiment tested ...",
                "on_topic": False,
                "subject": "YOYO experiment",
            },
        ]
    }


def test_counts_drift_and_on_topic_fraction():
    judge = RelevanceJudge(chat_fn=_stub_three_passages)

    score = judge.score(entity="Anthropic", page="<page md>")

    assert score.drift_count == 1
    assert score.on_topic_fraction == 2 / 3


def test_drift_subjects_names_the_off_topic_pollution():
    """Auditing handle: which subjects did the page drift into? (the YOYO case)."""
    judge = RelevanceJudge(chat_fn=_stub_three_passages)

    score = judge.score(entity="Anthropic", page="<page md>")

    assert score.drift_subjects == ["YOYO experiment"]


def test_malformed_judge_output_missing_passages_raises():
    """A judge LLM that returns no `passages` array failed — surface it as an error
    rather than crashing with a bare KeyError (mirrors faithfulness)."""

    def _no_passages(_prompt: str) -> dict:
        return {"summary": "I could not split this page."}

    judge = RelevanceJudge(chat_fn=_no_passages)

    with pytest.raises(ValueError, match="passages"):
        judge.score(entity="Anthropic", page="<page md>")
