"""Tests for evals.extraction.scorers.NarrativeCoverageScorer.

Per-gold-thread binary present/absent over narrative_md; coverage = present/total.
The LLM present/absent call is stubbed — the scorer carries no provider dep.
"""

import pytest
from evals.extraction.scorers import NarrativeCoverageScorer


def _stub_chat(present_map: dict):
    """Coverage-judge stub — returns a fixed {thread_index: 0/1} map regardless of prompt."""

    def chat(prompt: str) -> dict:
        return present_map

    return chat


def test_coverage_is_present_over_total():
    scorer = NarrativeCoverageScorer(chat_fn=_stub_chat({"0": 1, "1": 0, "2": 1}))
    score = scorer.score(
        expected={"gold_threads": ["thread a", "thread b", "thread c"]},
        actual={"narrative_md": "some narrative mentioning a and c"},
    )
    assert score.value["__overall__"] == pytest.approx(2 / 3, abs=1e-6)
    assert score.metadata["per_thread"] == {"thread a": 1.0, "thread b": 0.0, "thread c": 1.0}


def test_partial_or_stringy_verdicts_coerced():
    """Judge may return bools/strings; partial (<0.5) and unknowns count as absent."""
    scorer = NarrativeCoverageScorer(
        chat_fn=_stub_chat({"0": True, "1": "yes", "2": 0.4, "3": "no"})
    )
    score = scorer.score(
        expected={"gold_threads": ["a", "b", "c", "d"]},
        actual={"narrative_md": "x"},
    )
    assert score.value["__overall__"] == pytest.approx(2 / 4, abs=1e-6)


def test_broken_judge_raises_not_zero_coverage():
    """A persistently empty/malformed judge map must fail loudly, not look like a real 0%."""
    scorer = NarrativeCoverageScorer(chat_fn=_stub_chat({}))
    with pytest.raises(ValueError, match="not a real 0%"):
        scorer.score(
            expected={"gold_threads": ["a", "b"]},
            actual={"narrative_md": "x"},
        )


def test_incomplete_judge_map_is_retried_then_succeeds():
    """A transient incomplete map (missing keys) is retried; a later complete map wins."""
    responses = [{"0": 1}, {"0": 1, "1": 0}]  # first drops key 1, retry is complete

    def flaky_chat(prompt: str) -> dict:
        return responses.pop(0)

    scorer = NarrativeCoverageScorer(chat_fn=flaky_chat, max_retries=2)
    score = scorer.score(
        expected={"gold_threads": ["a", "b"]},
        actual={"narrative_md": "x"},
    )
    assert score.value["__overall__"] == pytest.approx(0.5, abs=1e-6)


def test_one_based_judge_response_raises():
    """A 1-based verdict map is a misalignment — thread 0's key is missing → raise."""
    scorer = NarrativeCoverageScorer(chat_fn=_stub_chat({"1": 1, "2": 1}))
    with pytest.raises(ValueError, match="misaligned"):
        scorer.score(
            expected={"gold_threads": ["a", "b"]},
            actual={"narrative_md": "x"},
        )


def test_score_run_pulls_from_fixture_and_run():
    """score_run is the selection adapter run_benchmark calls — no topic-card knowledge."""
    from evals.core import FixtureRun, RunStatus
    from evals.extraction.types import ExtractionFixture

    scorer = NarrativeCoverageScorer(chat_fn=_stub_chat({"0": 1, "1": 1}))
    fixture = ExtractionFixture(
        fixture_id="f",
        content_type="youtube",
        content="c",
        expected_topic_card={},
        gold_threads=["a", "b"],
    )
    run = FixtureRun(
        fixture_id="f",
        status=RunStatus.SUCCESS,
        output={"narrative_md": "covers a and b"},
        stages=[],
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        duration_ms=1,
    )
    score = scorer.score_run(fixture=fixture, run=run)
    assert score.value["__overall__"] == pytest.approx(1.0, abs=1e-6)
