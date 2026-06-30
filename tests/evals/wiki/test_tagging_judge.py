"""TaggingJudge — does the producer's [reported]/[opinion] tag match the source?
Verdicts are keyed by claim number, so the LLM returning extra/duplicate verdicts
(an off-by-one on long lists) is tolerated; a genuinely missing claim still raises.
Score logic TDD'd with a fake chat_fn; the real judge run is empirical."""

import pytest
from domains.wiki.source_summary import SourceClaim
from evals.wiki.judges import TaggingJudge


def _claims():
    return [
        SourceClaim(text="PAT achieves 34% on SPOT.", source_id="s", speculative=False),
        SourceClaim(text="AI will transform every field.", source_id="s", speculative=False),
        SourceClaim(text="Compute will stay scarce.", source_id="s", speculative=True),
    ]


def test_tagging_judge_scores_agreement_per_claim():
    # Judge correct tags (by claim number): 1=reported, 2=opinion, 3=opinion.
    # Producer tags:                         1=reported, 2=reported,        3=opinion → 2/3 agree.
    def fake_chat(prompt):
        return {
            "verdicts": [
                {"claim_number": 1, "correct_tag": "reported"},
                {"claim_number": 2, "correct_tag": "opinion"},
                {"claim_number": 3, "correct_tag": "opinion"},
            ]
        }

    score = TaggingJudge(chat_fn=fake_chat).score(claims=_claims(), source="the source body")

    assert score.accuracy == pytest.approx(2 / 3)
    disagree = [v for v in score.verdicts if not v.agree]
    assert len(disagree) == 1
    assert disagree[0].text == "AI will transform every field."


def test_tagging_judge_tolerates_extra_verdicts():
    # The LLM returned a spurious 4th verdict (out of range) — ignored, not fatal.
    def fake_chat(prompt):
        return {
            "verdicts": [
                {"claim_number": 1, "correct_tag": "reported"},
                {"claim_number": 2, "correct_tag": "reported"},
                {"claim_number": 3, "correct_tag": "opinion"},
                {"claim_number": 4, "correct_tag": "reported"},  # spurious
            ]
        }

    score = TaggingJudge(chat_fn=fake_chat).score(claims=_claims(), source="body")
    assert score.accuracy == 1.0
    assert len(score.verdicts) == 3


def test_tagging_judge_raises_when_a_claim_has_no_verdict():
    def fake_chat(prompt):
        return {"verdicts": [{"claim_number": 1, "correct_tag": "reported"}]}  # 2 and 3 missing

    with pytest.raises(ValueError, match="missing"):
        TaggingJudge(chat_fn=fake_chat).score(claims=_claims(), source="body")
