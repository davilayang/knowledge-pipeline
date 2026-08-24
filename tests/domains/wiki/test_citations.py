"""Citation checking — does a claim's text hold up against the units it cites?

The checks are lexical and free: a claim's numbers and proper nouns must appear
in the units it points at. Ported from newsletter-assistant's verifier lexical
baseline, whose thresholds were tuned against real content — the heuristics
here (sentence-initial capitals ignored, plural drift tolerated, numbers matched
on boundaries) exist to keep false rejections near zero.
"""

from domains.wiki.citations import check_citations, summarise_citations
from domains.wiki.claims import SourceClaim


def _claim(text: str, units: tuple[int, ...]) -> SourceClaim:
    return SourceClaim(text=text, source_id="medium::https://x.com/a", cited_units=units)


def test_claim_whose_figure_appears_in_its_cited_unit_is_grounded():
    units = ["Anthropic trained the model on 8 GPUs.", "Unrelated sentence."]

    (result,) = check_citations([_claim("Trained on 8 GPUs.", (0,))], units)

    assert result.status == "grounded"


def test_claim_whose_figure_is_absent_from_its_cited_unit_is_unsupported():
    units = ["Anthropic trained the model on 8 GPUs.", "Unrelated sentence."]

    (result,) = check_citations([_claim("Trained on 64 GPUs.", (0,))], units)

    assert result.status == "unsupported"


def test_a_figure_does_not_match_inside_a_longer_number():
    # "4" must not ground against source "64" — the boundary guard is the whole
    # point of a lexical check that is allowed to be trusted.
    units = ["Anthropic trained the model on 64 GPUs."]

    (result,) = check_citations([_claim("Trained on 4 GPUs.", (0,))], units)

    assert result.status == "unsupported"


def test_claim_whose_proper_noun_is_absent_from_its_cited_unit_is_unsupported():
    units = ["Anthropic shipped subagents in March."]

    (result,) = check_citations([_claim("Google shipped subagents.", (0,))], units)

    assert result.status == "unsupported"


def test_claim_citing_nothing_cannot_be_checked_and_is_uncited():
    (result,) = check_citations([_claim("Trained on 8 GPUs.", ())], ["Some unit."])

    assert result.status == "uncited"


def test_claim_citing_a_unit_that_does_not_exist_is_dangling():
    (result,) = check_citations([_claim("Trained on 8 GPUs.", (7,))], ["Only unit."])

    assert result.status == "dangling"


def test_claim_with_no_figure_or_proper_noun_is_reported_as_unchecked():
    # Nothing to match on, so it passes vacuously. Counting it as grounded would
    # inflate the score with claims the check never actually tested.
    units = ["The approach works well in practice."]

    (result,) = check_citations([_claim("The approach works well.", (0,))], units)

    assert result.status == "unchecked"


def test_a_sentence_initial_capital_is_not_treated_as_a_proper_noun():
    # "Trained" is capitalised only because it starts the claim; requiring it in
    # the source would reject an honest paraphrase.
    units = ["The team trained the model on 8 GPUs."]

    (result,) = check_citations([_claim("Trained on 8 GPUs.", (0,))], units)

    assert result.status == "grounded"


def test_a_claim_spanning_two_units_is_checked_against_both():
    units = ["Anthropic shipped subagents.", "The rollout took 2 weeks."]

    (result,) = check_citations([_claim("Anthropic shipped subagents in 2 weeks.", (0, 1))], units)

    assert result.status == "grounded"


def test_summarises_a_stored_claim_doc_against_the_source_body():
    body = "Anthropic trained the model on 8 GPUs. The rollout took two weeks."
    claims_doc = (
        "---\nitem_id: medium::https://x.com/a\ncontent_date: '2026-03-15'\n---\n\n"
        "- [reported|0] Anthropic trained the model on 8 GPUs.\n"
        "- [reported|0] Anthropic trained the model on 64 GPUs.\n"
        "- [reported] Anthropic shipped subagents.\n"
    )

    summary = summarise_citations(claims_doc, body)

    assert (summary.total, summary.grounded, summary.unsupported, summary.uncited) == (3, 1, 1, 1)


def test_an_unchecked_claim_is_not_reported_as_a_failing_example():
    # Nothing to match on is not an objection — listing it would bury the real
    # fabrications under claims the check simply had no grip on.
    body = "The approach works well in practice."
    claims_doc = (
        "---\nitem_id: medium::https://x.com/a\ncontent_date: null\n---\n\n"
        "- [opinion|0] The approach works well.\n"
    )

    summary = summarise_citations(claims_doc, body)

    assert (summary.unchecked, summary.failing, summary.failing_examples) == (1, 0, [])
