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
    tail_hits = 0
    redundancy = 0
    for claim in grounded:
        deciles = _deciles(claim.cited_indices, n) if n else set()
        if any(d >= 7 for d in deciles):
            tail_hits += 1
        if deciles and deciles <= covered:
            redundancy += 1  # cites only already-covered regions — padding
        covered |= deciles

    supported = len(grounded)
    return {
        "supported_claims": supported,
        "unsupported_claims": len(ungrounded),
        "distinct_span_coverage": len(covered) / 10,
        "tail_coverage": (tail_hits / supported) if supported else 0.0,
        "redundancy": redundancy,
    }
