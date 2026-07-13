"""Tests for evals.extraction.verify — Layer-1 faithfulness check.

Ported from NA's grounding.verify_grounding: a claim is grounded iff its hard
tokens (numbers / quoted spans) and entity words (capitalized) are present in its
cited units. Independent of the model's own index claim (a model can assert a
claim AND cite a wrong-but-plausible unit).
"""

from evals.extraction.verify import verify_grounding
from evals.extraction.wide import Claim


def test_claim_grounded_when_token_present_in_cited_unit():
    units = ["The system used 64 GPUs.", "Unrelated sentence."]
    claim = Claim(text="It used 64 GPUs.", cited_indices=[0])
    grounded, ungrounded = verify_grounding([claim], units)
    assert grounded == [claim]
    assert ungrounded == []


def test_claim_ungrounded_when_token_absent_from_cited_unit():
    units = ["The system used 64 GPUs.", "Unrelated sentence."]
    claim = Claim(text="It used 128 GPUs.", cited_indices=[1])
    grounded, ungrounded = verify_grounding([claim], units)
    assert grounded == []
    assert ungrounded == [claim]


def test_empty_cited_indices_is_ungrounded():
    units = ["Anything."]
    claim = Claim(text="Some claim about 5 things.", cited_indices=[])
    grounded, ungrounded = verify_grounding([claim], units)
    assert ungrounded == [claim]


def test_out_of_range_index_is_ungrounded():
    units = ["Only unit zero."]
    claim = Claim(text="Cites a dangling pointer.", cited_indices=[7])
    grounded, ungrounded = verify_grounding([claim], units)
    assert ungrounded == [claim]
