"""Tests for evals.extraction.coverage — index-decile coverage over cited claims.

Localization is exact: a claim's cited unit index maps to a source decile, no
text matching. Only *supported* claims (faithful per verify_grounding) count
toward coverage, so a fabricated claim citing a tail unit can't inflate the tail.
"""

from evals.extraction.coverage import coverage
from evals.extraction.wide import Claim

# 10 units, one per decile — unit i lands in decile i.
UNITS = [f"Unit number {i} says thing {i}." for i in range(10)]


def _claim(idx: int, tok: int | None = None) -> Claim:
    # Cite unit idx; embed unit's own number as a hard token so it verifies as
    # grounded against that unit (unless tok overrides to force an unsupported claim).
    n = idx if tok is None else tok
    return Claim(text=f"A claim mentioning {n}.", cited_indices=[idx])


def test_distinct_span_coverage_and_tail_coverage():
    cov = coverage(UNITS, [_claim(0), _claim(1), _claim(7), _claim(9)])
    assert cov["distinct_span_coverage"] == 0.4  # deciles {0,1,7,9}
    assert cov["tail_coverage"] == 2 / 3  # 2 of the 3 tail deciles (7,9) grounded
    assert cov["supported_claims"] == 4


def test_tail_coverage_is_volume_independent():
    # Same tail deciles grounded in both, but the second run piles on early-decile
    # claims. As a claim-count ratio the tail metric would sag; as the fraction of
    # tail deciles (7,8,9) grounded it must not move — an arm that emits more claims
    # can't look worse at the tail just for being verbose.
    tail_only = coverage(UNITS, [_claim(7), _claim(8), _claim(9)])
    with_early = coverage(UNITS, [_claim(7), _claim(8), _claim(9), _claim(0), _claim(1), _claim(2)])
    assert tail_only["tail_coverage"] == with_early["tail_coverage"] == 1.0


def test_unsupported_claim_excluded_from_coverage():
    # Cites unit 8 (tail) but its token (999) isn't in unit 8 → unsupported →
    # must not count toward tail_coverage.
    cov = coverage(UNITS, [_claim(0), _claim(8, tok=999)])
    assert cov["supported_claims"] == 1
    assert cov["unsupported_claims"] == 1
    assert cov["tail_coverage"] == 0.0


def test_same_decile_duplicate_is_redundancy_not_extra_coverage():
    cov = coverage(UNITS, [_claim(3), _claim(3), _claim(3)])
    assert cov["distinct_span_coverage"] == 0.1  # one decile
    assert cov["redundancy"] == 2
