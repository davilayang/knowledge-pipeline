"""Tests for the shared input-vs-output fidelity scorer."""

import pytest

from fetcher.fidelity import trigram_recall


def test_verbatim_copy_scores_one():
    text = "The engine selects all matching target columns from the mapping file."
    assert trigram_recall(text, text) == 1.0


def test_source_shorter_than_one_trigram_scores_one():
    """A source with no trigram to lose cannot have lost one — and must not divide by zero."""
    assert trigram_recall("Introduction", "Introduction") == 1.0


def test_dropping_repeats_of_a_block_is_counted_as_loss():
    """Keeping one of several near-identical blocks and dropping the rest is the
    exact failure the structurer prompt's verbatim-code-block rule targets. A
    set-membership check cannot see it: one surviving copy satisfies every
    occurrence in the source, scoring a perfect 1.0."""
    block = "pip install the-package version two"
    source = "\n".join([block] * 5)
    kept_one = block
    assert trigram_recall(source, kept_one) == pytest.approx(0.2)
