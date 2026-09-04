"""Tests for evals.extraction.scorers.BeatSupportScorer.

A delivery beat states the point of several load-bearing claims and names them
on a `From claims:` line. That compression is where invention becomes possible:
a beat can assert a cause, a consequence, or a connection none of its claims
carries, and the listener hears it in a voice channel with nothing to check it
against. This scorer is the check. The judge call is stubbed — the scorer
carries no provider dependency.
"""

from evals.extraction.scorers import BeatSupportScorer

_NARRATIVE = """Structure:
one throughline - routing beats scale

Load bearing claims (3):
1. A router beats one large model - 61% spend cut at Latchkey
2. Latency budget is 400ms end to end - measured on an A100
3. Routing needs traffic data first - Raghunathan

Delivery beats (2):
1. Routing wins on cost before it wins on anything else.
Anchor: 61% spend cut
From claims: 1
2. You cannot route without knowing your traffic, and the latency budget is
what decides whether you can afford to measure it.
Anchor: 400ms end to end
From claims: 2, 3
"""


def _stub_judge(verdicts: dict):
    def judge(prompt: str) -> dict:
        return verdicts

    return judge


def test_a_beat_asserting_what_its_claims_do_not_carry_scores_zero():
    """The scorer's whole purpose: catch a beat that invented the link between
    the claims it merged.

    Beat 2 above joins the traffic-data claim and the latency claim with a
    causal reading — that the budget decides whether measuring is affordable —
    which neither claim states. Nothing else in the pipeline can see this: the
    coverage scorer measures recall over reference threads and has no term for
    material the narrative adds, and the schema only checks that the cited
    claims exist.
    """
    scorer = BeatSupportScorer(chat_fn=_stub_judge({"1": 1, "2": 0}))
    score = scorer.score(actual={"narrative_md": _NARRATIVE})

    assert score.value["__overall__"] == 0.5
    assert score.metadata["per_beat"] == {"1": 1.0, "2": 0.0}
