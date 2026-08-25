"""Split a source body into numbered citable units — the addresses a claim cites.

An extracted claim carries the indices of the units it came from, so a verifier
can check the claim text against those spans instead of re-reading the article
with a judge. Pure — no LLM, no I/O.

Units are DERIVED on demand, never stored, which is only sound while a stored
citation and a fresh split agree. Two things break that agreement, and both
silently reindex every citation already stored rather than failing:

- **The body changing under a stored citation.** `fetch_content` returns early
  when `raw_content` is already set, but that is an asset-level guard, not a
  store invariant — `queue_store.upsert_fetched` overwrites `raw_content` on
  conflict, so a row whose body is cleared and re-fetched gets a new one.
- **This function changing.** Any edit to the split moves the indices under
  citations extracted by the previous version.

Neither matters while a citation is checked in the same run that produced it,
which is how the extract-time check uses it. A consumer reading citations
recorded earlier needs to pin the body hash and a version for this splitter
first.
"""

import re

_MAX_UNIT_CHARS = 500  # a unit longer than this won't localise a citation usefully
_WINDOW_CHARS = 320


def _windows(text: str, size: int = _WINDOW_CHARS) -> list[str]:
    """Break `text` into ~`size`-char chunks on word boundaries.

    Rebuilding from `split()` collapses runs of whitespace, so a re-cut unit is
    not a verbatim substring of the body while a sentence-path unit is. Nothing
    reads units by offset today; anything that starts to would need to match on
    the unit text rather than seek it in the body."""
    out: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + len(word) + 1 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


_STRUCTURAL = re.compile(r"^\s*(?:[-*+]\s|#{1,6}\s|>|\||\d+[.)]\s|```|---+\s*$|\$\$)")


def _blocks(content_md: str) -> list[str]:
    """Group the body into pieces that a sentence split can safely run over.

    A STRUCTURAL line — heading, list item, table row, quote, fence, rule,
    display equation — is its own block, because it carries no terminal
    punctuation and would otherwise glue onto whatever follows, putting a whole
    section behind one index. Consecutive prose lines are rejoined into one
    block: caption tracks and hard-wrapped paragraphs break mid-sentence, and
    treating every newline as a boundary cut 1,104 sentences in half across the
    transcripts alone."""
    out: list[str] = []
    prose: list[str] = []
    for line in content_md.splitlines():
        if not line.strip():
            structural = True
        else:
            structural = bool(_STRUCTURAL.match(line))
        if structural:
            if prose:
                out.append(" ".join(prose))
                prose = []
            if line.strip():
                out.append(line.strip())
        else:
            prose.append(line.strip())
    if prose:
        out.append(" ".join(prose))
    return out


def build_citable_units(content_md: str) -> list[str]:
    """Split a source body into citation targets — one per structural line, one
    per sentence of prose, and any sentence over 500 chars re-cut into
    word-boundary windows.

    Structure is a boundary because the numbered body tells the model each line
    starts with its index, and sentence punctuation alone cannot honour that:
    headings, list items and table rows carry no terminal punctuation, so they
    glue onto whatever follows and a citation lands on a whole section rather
    than a line. Prose lines are rejoined first, so a wrapped sentence is not
    cut in half. Either way a unit carries no embedded newline.

    The re-cut is what makes this survive auto-captioned transcripts, which can
    run tens of KB with almost no sentence-ending punctuation: without it a
    citation would point at a unit far too large to check a claim against.
    500 is a target, not a hard cap: a single unbroken token (a long URL) has
    no whitespace to cut on and rides through at full length."""
    units: list[str] = []
    for block in _blocks(content_md):
        for sentence in re.split(r"(?<=[.!?])\s+", block):
            sentence = sentence.strip()
            if not sentence:
                continue
            units.extend(_windows(sentence) if len(sentence) > _MAX_UNIT_CHARS else [sentence])
    return units


def number_units(units: list[str]) -> str:
    """Render units as the numbered body the extractor reads — `[0] text` per
    line. The index prefix is the address a claim cites back to."""
    return "\n".join(f"[{i}] {unit}" for i, unit in enumerate(units))
