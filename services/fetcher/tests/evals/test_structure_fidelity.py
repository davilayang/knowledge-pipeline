"""Tests for the structurer-fidelity scorer."""

import pytest

from evals.structure_fidelity import positional_recall, trigram_recall


def test_verbatim_copy_scores_one():
    text = "The engine selects all matching target columns from the mapping file."
    assert trigram_recall(text, text) == 1.0


def test_source_shorter_than_one_trigram_scores_one():
    """A source with no trigram to lose cannot have lost one — and must not divide by zero."""
    assert trigram_recall("Introduction", "Introduction") == 1.0


def test_merged_and_paraphrased_sentences_score_far_below_verbatim():
    """The failure this scorer exists to catch: two source sentences rewritten as
    one. Taken verbatim from section 5.2 of the Medium article
    "Why my enterprise AI system needed a deterministic analysis layer", whose
    stored structurer output merged them."""
    source = (
        "This Selection_Rule is handed over to P_Analysis_Engine for execution. "
        "First, the engine selects all matching target columns from the Mapping_File."
    )
    merged = (
        "The `P_Analysis_Engine` first selects matching target columns "
        "from the `Mapping_File`, such as:"
    )
    assert trigram_recall(source, merged) < 0.5


def test_positional_recall_localises_loss_to_the_end_of_the_document():
    """Loss concentrated late is the structurer's signature failure, and a single
    overall score hides it. Ten distinct lines, of which the output keeps only the
    first five: the first half of the curve is intact, the second half is empty."""
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet".split()
    lines = [f"{w} {w}wards {w}ology {w}ation {w}ising" for w in words]
    source = "\n".join(lines)
    structured = "\n".join(lines[:5])
    curve = positional_recall(source, structured, buckets=2)
    assert curve == [1.0, 0.0]


def test_overall_score_is_the_aggregate_of_the_curve():
    """The two entry points must never disagree: a reader comparing an overall
    score against its own positional curve would otherwise be misled. Pinned
    because they were computed over different denominators when first written."""
    source = "\n".join(f"{w} {w}wards {w}ology {w}ation" for w in "alpha bravo charlie".split())
    structured = "alpha alphawards alphaology alphaation\nbravo bravowards"
    assert positional_recall(source, structured, buckets=1) == [trigram_recall(source, structured)]


def test_dropping_repeats_of_a_block_is_counted_as_loss():
    """Keeping one of several near-identical blocks and dropping the rest is the
    exact failure the structurer prompt's verbatim-code-block rule targets. A
    set-membership check cannot see it: one surviving copy satisfies every
    occurrence in the source, scoring a perfect 1.0."""
    block = "pip install the-package version two"
    source = "\n".join([block] * 5)
    kept_one = block
    assert trigram_recall(source, kept_one) == pytest.approx(0.2)
