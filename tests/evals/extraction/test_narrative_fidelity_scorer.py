"""Tests for evals.extraction.fidelity — the three-metric narrative-fidelity scorer.

faithful_recall (omission) / distortion_rate (corruption) / fabrication_rate
(invention), with conservative two-juror aggregation (false-pass-averse floor).
Judge calls are injected + stubbed — the scorer carries no provider dependency.
"""

import pytest
from evals.extraction.fidelity import (
    conservative_merge,
    distortion_rate,
    fabrication_rate,
    faithful_recall,
    merge_fidelity_verdicts,
    merge_invented,
    severe_omission_count,
)
from evals.extraction.scorers import NarrativeFidelityScorer


def test_faithful_recall_counts_only_faithful():
    """faithful_recall = faithful / total gold threads.

    Omission (`absent`) and corruption (`distorted`) both fail to recall the
    thread faithfully, so only `faithful` verdicts count toward recall.
    """
    assert faithful_recall(["faithful", "distorted", "absent", "faithful"]) == 0.5


def test_distortion_rate_over_present_threads_only():
    """distortion_rate = distorted / present, where present = faithful + distorted.

    An `absent` thread is an omission (counted by recall), not a corruption — so
    it's excluded from the distortion denominator, not scored as faithful.
    """
    # 1 distorted of 2 present (the absent one drops out) → 0.5
    assert distortion_rate(["distorted", "faithful", "absent"]) == 0.5


def test_fabrication_rate_over_produced_threads():
    """fabrication_rate = invented / total produced threads.

    Judged extraction→source: each produced thread is flagged True when its
    content isn't supported by the source (an invented claim/figure/entity).
    """
    # 1 invented of 4 produced → 0.25
    assert fabrication_rate([False, True, False, False]) == 0.25


def test_severe_omission_counts_absent_critical_threads_only():
    """Severe omission (the severe-omission rule) = a *critical* thread that is *absent*.

    A non-critical absent thread is a minor omission; a critical thread that is
    *distorted* is a severe distortion, not an omission — so neither counts here.
    """
    verdicts = ["absent", "faithful", "absent", "distorted"]
    # critical = [1, 2, 3]; only index 2 is both critical AND absent → 1.
    # index 0 (absent, non-critical) = minor; index 3 (distorted, critical) = a distortion.
    assert severe_omission_count(verdicts, [1, 2, 3]) == 1


def test_scorer_reports_faithful_recall_from_judge_verdicts():
    """The scorer batches gold threads to an injected fidelity judge and turns
    its per-thread verdicts into faithful_recall in the FieldScore."""

    def fake_fidelity(_prompt: str) -> dict:
        return {"0": "faithful", "1": "absent", "2": "faithful"}

    scorer = NarrativeFidelityScorer(
        fidelity_chat_fn=fake_fidelity,
        fabrication_chat_fn=lambda _p: {},
    )
    score = scorer.score(
        expected={"gold_threads": ["t0", "t1", "t2"], "critical_threads": [], "source": "src"},
        actual={"narrative_md": "candidate", "threads": []},
    )
    assert score.value["faithful_recall"] == pytest.approx(2 / 3)


def test_scorer_reports_distortion_rate_from_same_verdicts():
    """distortion_rate comes from the same fidelity call — no extra judge round."""

    def fake_fidelity(_prompt: str) -> dict:
        return {"0": "faithful", "1": "distorted", "2": "absent"}

    scorer = NarrativeFidelityScorer(
        fidelity_chat_fn=fake_fidelity,
        fabrication_chat_fn=lambda _p: {},
    )
    score = scorer.score(
        expected={"gold_threads": ["a", "b", "c"], "critical_threads": [], "source": "s"},
        actual={"narrative_md": "n", "threads": []},
    )
    # present = faithful + distorted = 2; distorted = 1 → 0.5
    assert score.value["distortion_rate"] == 0.5


def test_scorer_reports_severe_omissions_over_critical_threads():
    """The tripwire input: only absent *critical* threads count as severe."""

    def fake_fidelity(_prompt: str) -> dict:
        return {"0": "absent", "1": "faithful", "2": "absent"}

    scorer = NarrativeFidelityScorer(
        fidelity_chat_fn=fake_fidelity,
        fabrication_chat_fn=lambda _p: {},
    )
    score = scorer.score(
        expected={"gold_threads": ["a", "b", "c"], "critical_threads": [0, 1], "source": "s"},
        actual={"narrative_md": "n", "threads": []},
    )
    # critical = [0, 1]; absent-and-critical = index 0 only → 1
    # (index 2 is absent but non-critical → minor, not severe)
    assert score.value["severe_omissions"] == 1


def test_scorer_reports_fabrication_rate_from_fabrication_judge():
    """A second judge flags produced threads not supported by the source."""

    def fake_fabrication(_prompt: str) -> dict:
        return {"0": False, "1": True, "2": False, "3": False}

    scorer = NarrativeFidelityScorer(
        fidelity_chat_fn=lambda _p: {"0": "faithful"},
        fabrication_chat_fn=fake_fabrication,
    )
    score = scorer.score(
        expected={"gold_threads": ["g"], "critical_threads": [], "source": "the source text"},
        actual={"narrative_md": "n", "threads": ["p0", "p1", "p2", "p3"]},
    )
    # 1 invented of 4 produced → 0.25
    assert score.value["fabrication_rate"] == 0.25


def test_merge_fidelity_verdicts_elementwise_conservative():
    """Two jurors' verdict lists fold to the per-thread lower fidelity."""
    a = ["faithful", "faithful", "distorted"]
    b = ["distorted", "faithful", "faithful"]
    assert merge_fidelity_verdicts(a, b) == ["distorted", "faithful", "distorted"]


def test_merge_invented_flags_when_either_juror_does():
    """Fabrication is false-pass-averse: either juror flagging invention counts."""
    assert merge_invented([False, True, False], [False, False, True]) == [False, True, True]


def test_conservative_merge_takes_lower_fidelity_on_disagreement():
    """Floor is false-pass-averse: never credit fidelity a juror doubts.

    On the lattice absent < distorted < faithful, disagreement resolves to the
    lower (more-pessimistic) verdict, so a self-preferring juror can only add a
    caught failure, never hide one.
    """
    assert conservative_merge("faithful", "distorted") == "distorted"


def test_conservative_merge_full_lattice():
    """Exhaustive: min over absent < distorted < faithful, order-independent."""
    # agreement → that verdict
    assert conservative_merge("faithful", "faithful") == "faithful"
    assert conservative_merge("distorted", "distorted") == "distorted"
    assert conservative_merge("absent", "absent") == "absent"
    # disagreement → lower fidelity, both argument orders
    assert conservative_merge("faithful", "absent") == "absent"
    assert conservative_merge("absent", "faithful") == "absent"
    assert conservative_merge("distorted", "absent") == "absent"
    assert conservative_merge("absent", "distorted") == "absent"
    assert conservative_merge("distorted", "faithful") == "distorted"
