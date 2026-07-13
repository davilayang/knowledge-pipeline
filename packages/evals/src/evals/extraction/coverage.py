"""Index-decile coverage metric for cite-by-index extraction runs.

Deterministic, no text matching. Localization is exact: a claim's cited unit
index maps to a source decile (`idx * 10 // n_units`). Faithfulness is delegated
to verify_grounding — only *supported* (faithful) claims count toward coverage,
so a fabricated claim citing a tail unit can't inflate the tail. The headline
`distinct_span_coverage` counts distinct source deciles grounded; `tail_coverage`
exposes the document tail single-pass extraction drops.

This is a workbench function, not a benchmark scorer: its signature is
`coverage(units, claims)` (one run vs its numbered source), a different shape from
the benchmark `score(expected, actual)` contract.
"""

from evals.extraction.verify import verify_grounding
from evals.extraction.wide import Claim


def _deciles(cited_indices: list[int], n_units: int) -> set[int]:
    return {min(9, i * 10 // n_units) for i in cited_indices}


def coverage(units: list[str], claims: list[Claim]) -> dict:
    n = len(units)
    grounded, ungrounded = verify_grounding(claims, units)

    covered: set[int] = set()
    redundancy = 0
    for claim in grounded:
        deciles = _deciles(claim.cited_indices, n) if n else set()
        if deciles and deciles <= covered:
            # Cites only already-covered regions — padding. NOTE: under layered
            # extraction with chunk overlap + concat merge this also counts the
            # same claim extracted from two overlapping windows, so it conflates
            # windowing artifact with genuine model repetition until dedup collapses
            # the overlap duplicates first.
            redundancy += 1
        covered |= deciles

    # Denominators are the deciles actually *reachable* for this n, not constants:
    # `idx*10//n` can't hit every decile when n < 10 (e.g. n=4 → only {0,2,5,7}),
    # so dividing by 10 (or the tail's 3) would cap a fully-grounded short doc
    # below 1.0. For the long cohort (n ≥ 10) reachable == all 10, so this is a
    # no-op there and only fixes the short-doc case.
    reachable = {min(9, i * 10 // n) for i in range(n)} if n else set()
    tail = reachable & {7, 8, 9}
    return {
        "supported_claims": len(grounded),
        "unsupported_claims": len(ungrounded),
        "distinct_span_coverage": (len(covered) / len(reachable)) if reachable else 0.0,
        # Fraction of the tail deciles grounded — NOT tail-claims / all-claims. A
        # claim-count ratio sags when an arm emits more (early) claims; this
        # measures whether the document tail is reached, independent of volume.
        "tail_coverage": (len(covered & tail) / len(tail)) if tail else 0.0,
        "redundancy": redundancy,
    }
