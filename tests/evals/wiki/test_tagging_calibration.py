"""Tagging-judge calibration against a human gold set — does the judge's
correct_tag match the human label? Aggregation TDD'd with a fake judge."""

from evals.wiki.claims.calibration import GoldClaim, calibrate
from evals.wiki.judges import ClaimTagVerdict, TaggingScore


class _FakeJudge:
    """Returns the tags it's told to via `_canned`, in claim order."""

    def __init__(self, canned):
        self._canned = canned

    def score(self, *, claims, source):
        verdicts = [
            ClaimTagVerdict(text=c.text, producer_tag="reported", correct_tag=t, agree=True)
            for c, t in zip(claims, self._canned[source], strict=True)
        ]
        return TaggingScore(verdicts=verdicts, accuracy=1.0)


def test_calibrate_scores_judge_against_gold():
    gold = [
        GoldClaim("s1", "claim a", producer_tag="reported", gold_tag="reported"),
        GoldClaim("s1", "claim b", producer_tag="reported", gold_tag="opinion"),
        GoldClaim("s2", "claim c", producer_tag="opinion", gold_tag="opinion"),
    ]
    bodies = {"s1": "body one", "s2": "body two"}
    # Judge: s1 -> [reported, reported], s2 -> [opinion]. Gold: [reported, opinion, opinion].
    # Agrees on a (reported) and c (opinion); disagrees on b (judge reported, gold opinion).
    judge = _FakeJudge({"body one": ["reported", "reported"], "body two": ["opinion"]})

    result = calibrate(gold, bodies, judge=judge)

    assert result.n == 3
    assert result.judge_accuracy == 2 / 3
    assert len(result.disagreements) == 1
    d = result.disagreements[0]
    assert d.claim_text == "claim b"
    assert d.judge_tag == "reported"
    assert d.gold_tag == "opinion"
