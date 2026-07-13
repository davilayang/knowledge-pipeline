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
            redundancy += 1  # cites only already-covered regions — padding
        covered |= deciles

    tail = {7, 8, 9}
    return {
        "supported_claims": len(grounded),
        "unsupported_claims": len(ungrounded),
        "distinct_span_coverage": len(covered) / 10,
        # Fraction of the tail's 3 deciles grounded — NOT tail-claims / all-claims.
        # A claim-count ratio sags when an arm emits more (early) claims; this
        # measures whether the document tail is reached, independent of volume.
        "tail_coverage": len(covered & tail) / len(tail),
        "redundancy": redundancy,
    }
