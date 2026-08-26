"""Lexical fidelity between a structurer's input and its output.

Both structurers clean text without rewriting it, so length can't tell if a
model summarised: one transcript kept 92.5% of its length while preserving
only 54.8% of its wording. Trigram recall measures wording survival directly;
repeats are clipped so one of five identical blocks scores as one, not five.

Shared by the production retention guard and the offline eval harness, so a
single implementation defines the number both report.
"""

import re
from collections import Counter


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def _trigrams(text: str) -> list[tuple[str, str, str]]:
    t = _tokens(text)
    return list(zip(t, t[1:], t[2:], strict=False))


def trigram_recall(source: str, produced: str) -> float:
    """Share of the source's word-trigrams that survive into `produced`.

    Counted per source line, so trigrams straddling a line break (which the
    structurer reflows) don't add a constant noise floor. Lines too short to
    form a trigram contribute nothing.

    Counts correctly-removed boilerplate as loss, since the denominator is the
    whole source — so a floor set against it must leave room for whatever
    chrome the lane legitimately strips, and scores only compare within a
    fixture. Position-blind: text that survived but moved still counts.
    """
    remaining = Counter(_trigrams(produced))
    hits = total = 0
    for line in source.split("\n"):
        for trigram in _trigrams(line):
            total += 1
            if remaining[trigram] > 0:
                remaining[trigram] -= 1
                hits += 1
    if not total:
        return 1.0
    return hits / total


def long_gaps_per_10k(source: str, produced: str, *, gap_trigrams: int = 15) -> float:
    """Contiguous runs of lost wording, per 10,000 source characters.

    `gap_trigrams` counts consecutive missing **trigrams**, not words. Trigrams
    overlap, so deleting K consecutive words breaks K+2 of them: the K starting
    inside the deleted span plus the 2 that straddled its edges. The default of
    15 fires on roughly 13 deleted words.

    Separates the two ways a structurer loses text, which recall alone
    conflates: removing disfluency scatters many one- and two-word gaps;
    rewriting a passage leaves one long contiguous gap. Across the transcript
    corpus, faithful outputs sit under 3.3 per 10k and the two known rewrites
    sit at 8.7 and 22.6.

    Not meaningful for the article lane, where removing a nav block or a footer
    is a long contiguous gap and is the job.

    Working window is roughly 8-15, over which faithful and rewritten outputs
    stay separated by 2.4-2.8x. Raising `gap_trigrams` does NOT make the check
    stricter: at 20 the separation falls to 1.1x, and by 30 it inverts —
    rewritten outputs score *lower* than faithful ones, because their gaps are
    mostly 15-30 words and stop qualifying at all. Change this only alongside
    re-measuring the corpus and re-deriving the ceiling — the scale moves.

    Measured against only two documents confirmed as rewritten by reading their
    lost passages — a thin basis for the exact numbers, though the effect
    itself is unambiguous.
    """
    remaining = Counter(_trigrams(produced))
    gaps = 0
    run = 0
    for line in source.split("\n"):
        for trigram in _trigrams(line):
            if remaining[trigram] > 0:
                remaining[trigram] -= 1
                if run >= gap_trigrams:
                    gaps += 1
                run = 0
            else:
                run += 1
    if run >= gap_trigrams:
        gaps += 1
    return 10_000 * gaps / max(len(source), 1)
