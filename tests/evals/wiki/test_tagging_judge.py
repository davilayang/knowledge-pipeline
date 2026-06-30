"""TaggingJudge — does the producer's [fact]/[speculation] tag match the source?
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
    # Judge's correct tags: fact, speculation, speculation.
    # Producer tags:        fact, fact,        speculation  → 2/3 agree.
    def fake_chat(prompt):
        return {"verdicts": [{"correct_tag": t} for t in ("fact", "speculation", "speculation")]}

    score = TaggingJudge(chat_fn=fake_chat).score(claims=_claims(), source="the source body")

    assert score.accuracy == pytest.approx(2 / 3)
    disagree = [v for v in score.verdicts if not v.agree]
    assert len(disagree) == 1
    assert disagree[0].text == "AI will transform every field."
    assert disagree[0].producer_tag == "fact"
    assert disagree[0].correct_tag == "speculation"


def test_tagging_judge_rejects_mismatched_verdict_count():
    def fake_chat(prompt):
        return {"verdicts": [{"correct_tag": "fact"}]}  # only 1 for 3 claims

    with pytest.raises(ValueError, match="verdict"):
        TaggingJudge(chat_fn=fake_chat).score(claims=_claims(), source="body")
