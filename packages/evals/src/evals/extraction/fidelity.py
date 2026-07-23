"""Three-metric narrative-fidelity scorer + conservative two-juror aggregation.

Scores narrative_v2's summary on three orthogonal failure modes, cost-ranked
invention > corruption > omission (see the estimand card):

- faithful_recall  (omission)   — of gold threads, how many landed faithfully
- distortion_rate  (corruption) — of present gold threads, how many are mangled
- fabrication_rate (invention)  — of produced threads, how many aren't in source

Two-juror scoring (e.g. Opus + Codex) is merged conservatively: the floor is
false-pass-averse, so a self-preferring juror can only add a caught failure,
never hide one. Judge calls are injected so the scorer is provider-free and
stub-testable offline.
"""

# Per-thread fidelity lattice: absent (missing) < distorted (present but wrong)
# < faithful (present and right). Only `faithful` is a pass; the other two are
# failures (omission / corruption respectively).
_FIDELITY_ORDER = {"absent": 0, "distorted": 1, "faithful": 2}


def faithful_recall(verdicts: list[str]) -> float:
    """Fraction of gold threads recalled faithfully = faithful / total.

    Only `faithful` counts: `absent` is an omission, `distorted` is present but
    corrupted — neither recalls the thread's content faithfully. Empty → 0.0.
    """
    if not verdicts:
        return 0.0
    return sum(v == "faithful" for v in verdicts) / len(verdicts)


def distortion_rate(verdicts: list[str]) -> float:
    """Fraction of *present* gold threads that are corrupted = distorted / present.

    Present = faithful + distorted. `absent` threads are omissions (scored by
    recall) and drop out of the denominator — distortion measures corruption
    among threads the extractor actually surfaced. No present threads → 0.0.
    """
    present = sum(v in ("faithful", "distorted") for v in verdicts)
    if not present:
        return 0.0
    return sum(v == "distorted" for v in verdicts) / present


def fabrication_rate(invented: list[bool]) -> float:
    """Fraction of produced threads that are invented = invented / total produced.

    Judged extraction→source (not against gold): a produced thread is `True`
    when its content — a claim, figure, entity, or causal link — is not in the
    source. No produced threads → 0.0.
    """
    if not invented:
        return 0.0
    return sum(invented) / len(invented)


def severe_omission_count(verdicts: list[str], critical_indices: list[int]) -> int:
    """Number of severe omissions (codebook §4) = critical threads that are absent.

    Only a *critical* thread going *absent* is severe. A non-critical absent
    thread is a minor omission; a critical thread that is *distorted* is a severe
    distortion, not an omission. The tripwire gate keys on this count.
    """
    crit = set(critical_indices)
    return sum(v == "absent" for i, v in enumerate(verdicts) if i in crit)


def conservative_merge(a: str, b: str) -> str:
    """Merge two jurors' per-thread fidelity verdicts, taking the lower.

    On disagreement the floor is false-pass-averse — resolve to the lower
    (more-pessimistic) verdict on the absent < distorted < faithful lattice, so
    a self-preferring juror can only add a caught failure, never hide one.
    """
    return a if _FIDELITY_ORDER[a] <= _FIDELITY_ORDER[b] else b
