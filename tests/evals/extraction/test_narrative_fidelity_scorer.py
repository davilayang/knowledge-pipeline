"""Tests for evals.extraction.fidelity — the three-metric narrative-fidelity scorer.

faithful_recall (omission) / distortion_rate (corruption) / fabrication_rate
(invention), with conservative two-juror aggregation (false-pass-averse floor).
Judge calls are injected + stubbed — the scorer carries no provider dependency.
"""

from evals.extraction.fidelity import (
    conservative_merge,
    distortion_rate,
    fabrication_rate,
    faithful_recall,
    severe_omission_count,
)


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
    """Severe omission (codebook §4) = a *critical* thread that is *absent*.

    A non-critical absent thread is a minor omission; a critical thread that is
    *distorted* is a severe distortion, not an omission — so neither counts here.
    """
    verdicts = ["absent", "faithful", "absent", "distorted"]
    # critical = [1, 2, 3]; only index 2 is both critical AND absent → 1.
    # index 0 (absent, non-critical) = minor; index 3 (distorted, critical) = a distortion.
    assert severe_omission_count(verdicts, [1, 2, 3]) == 1


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
