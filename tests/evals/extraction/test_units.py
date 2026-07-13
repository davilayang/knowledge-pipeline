"""Tests for evals.extraction.units — source numbering for cite-by-index.

Mirrors NA's build_citable_units (sentence split + word-window fallback) so the
citation index shape stays aligned across the two repos.
"""

from evals.extraction.units import citable_units


def test_splits_into_one_unit_per_sentence():
    units = citable_units("First sentence here. Second one follows! Third?")
    assert units == ["First sentence here.", "Second one follows!", "Third?"]


def test_overlong_unpunctuated_unit_is_rechunked_into_windows():
    # A caption-style transcript run with no sentence punctuation — one giant
    # "sentence" must become several localisable word-window units.
    long_run = " ".join(f"word{i}" for i in range(300))  # ~2000 chars, no period
    units = citable_units(long_run)
    assert len(units) > 1
    assert all(len(u) <= 500 for u in units)
