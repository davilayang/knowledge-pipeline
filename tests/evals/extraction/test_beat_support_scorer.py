"""Tests for evals.extraction.scorers.BeatSupportScorer.

A beat can assert a cause or connection none of its cited claims carries, and
the listener hears it with nothing to check it against. This scorer is the
check. The judge call is stubbed.
"""

from evals.extraction.scorers import BeatSupportScorer

_NARRATIVE = """Structure:
one throughline - routing beats scale

Load bearing claims (3):
1. A router beats one large model - 61% spend cut at Latchkey
2. Latency budget is 400ms end to end - measured on an A100
3. Routing needs traffic data first - Raghunathan

Delivery beats (2):
1. Routing wins on cost before it wins on anything else. [Anchor: 61% spend cut] [From claims: 1]
2. Traffic data first; the budget decides affordability. [Anchor: 400ms] [From claims: 2, 3]
"""


def _stub_judge(verdicts: dict):
    def judge(prompt: str) -> dict:
        return verdicts

    return judge


def test_a_beat_asserting_what_its_claims_do_not_carry_scores_zero():
    """Beat 2 above reads a causal link into the two claims it merged — that the
    budget decides affordability — which neither states. Nothing else sees this:
    coverage has no term for what a narrative adds, and the schema only checks
    that a cited claim exists."""
    scorer = BeatSupportScorer(chat_fn=_stub_judge({"1": 1, "2": 0}))
    score = scorer.score(actual={"narrative_md": _NARRATIVE})

    assert score.value["__overall__"] == 0.5
    assert score.metadata["per_beat"] == {"1": 1.0, "2": 0.0}
