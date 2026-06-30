"""Tagging-judge calibration against a human gold set — does the judge's
correct_tag match the human label? Aggregation TDD'd with a fake judge."""

from evals.wiki.judges import ClaimTagVerdict, TaggingScore
from evals.wiki.source_summary.calibration import GoldClaim, calibrate


class _FakeJudge:
    """Returns the tags it's told to via `_canned`, in claim order."""

    def __init__(self, canned):
        self._canned = canned

    def score(self, *, claims, source):
        verdicts = [
            ClaimTagVerdict(text=c.text, producer_tag="fact", correct_tag=t, agree=True)
            for c, t in zip(claims, self._canned[source], strict=True)
        ]
        return TaggingScore(verdicts=verdicts, accuracy=1.0)


def test_calibrate_scores_judge_against_gold():
    gold = [
        GoldClaim("s1", "claim a", producer_tag="fact", gold_tag="fact"),
        GoldClaim("s1", "claim b", producer_tag="fact", gold_tag="speculation"),
        GoldClaim("s2", "claim c", producer_tag="speculation", gold_tag="speculation"),
    ]
    bodies = {"s1": "body one", "s2": "body two"}
    # Judge says: s1 -> [fact, fact], s2 -> [speculation]. Gold: [fact, spec, spec].
    # Judge matches gold on a (fact=fact) and c (spec=spec) but not b (judge fact, gold spec).
    judge = _FakeJudge({"body one": ["fact", "fact"], "body two": ["speculation"]})

    result = calibrate(gold, bodies, judge=judge)

    assert result.n == 3
    assert result.judge_accuracy == 2 / 3
    assert len(result.disagreements) == 1
    d = result.disagreements[0]
    assert d.claim_text == "claim b"
    assert d.judge_tag == "fact"
    assert d.gold_tag == "speculation"
