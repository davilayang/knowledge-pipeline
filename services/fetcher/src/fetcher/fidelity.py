"""Lexical fidelity between a structurer's input and its output.

Both structurers clean text without rewriting it, so the question "did the model
summarise?" is really "did the source's wording survive?". Length cannot answer
it: one production transcript kept 92.5% of its length while preserving 54.8% of
its wording, because the model paraphrased at roughly constant volume.

Trigram recall answers it directly. Repeats are clipped so an output keeping one
of five identical blocks scores those five as one, not five.

Shared by the production retention guard and the offline eval harness so a single
implementation defines the number both report.
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

    Counted per source line so that trigrams straddling a line break stay out of
    the denominator: the structurer reflows paragraphs, so those would miss on
    every run and add a constant noise floor. Lines too short to form a trigram
    contribute nothing.

    Counts correctly-removed boilerplate as loss, since the denominator is the
    whole source — so a floor set against it must leave room for whatever chrome
    the lane legitimately strips. Position-blind: text that survived but moved
    still counts.
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


def long_gaps_per_10k(source: str, produced: str, *, gap_words: int = 15) -> float:
    """Contiguous runs of lost wording, per 10,000 source characters.

    Distinguishes the two ways a structurer loses text, which recall alone
    conflates. Removing disfluency scatters many one- and two-word gaps; a
    heavily filler-laden talk can score 60% recall and still be faithful.
    Rewriting a passage leaves one long contiguous gap. Across the transcript
    corpus, faithful outputs sit under 3.3 per 10k and the two known rewrites
    sit at 8.7 and 22.6.

    Not meaningful for the article lane, where removing a nav block or a footer
    is a long contiguous gap and is the job.
    """
    remaining = Counter(_trigrams(produced))
    gaps = 0
    run = 0
    for line in source.split("\n"):
        for trigram in _trigrams(line):
            if remaining[trigram] > 0:
                remaining[trigram] -= 1
                if run >= gap_words:
                    gaps += 1
                run = 0
            else:
                run += 1
    if run >= gap_words:
        gaps += 1
    return 10_000 * gaps / max(len(source), 1)
