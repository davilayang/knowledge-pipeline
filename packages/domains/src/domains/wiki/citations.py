"""Check an extracted claim against the source units it cites.

A claim carries the indices of the units it came from (see domains.wiki.units).
This module answers, for free and without an LLM, whether the claim's hard
specifics — its numbers, its quoted spans, its proper nouns — actually appear in
those units. That catches the fabrication class worth catching: an invented
figure or a name attached to the wrong subject.

Only figures and quoted spans decide whether a claim is supported. Capitalised
words do not: measured over 66 real claims, requiring a claim's capitalised words
to appear in the source rejected acronym expansions ("Large Language Models"
against a transcript that only ever says "LLM") and inflected verbs ("Executing"
against a body saying "execute"), producing two false alarms and catching
nothing. Capitalisation cannot separate a name from a capitalised common noun, so
a swapped subject is left to the entailment tier. Capitalised words still count
toward localisation, where a miss costs precision rather than accusing the claim.

The matching rules are ported from newsletter-assistant's verifier lexical
baseline, tuned against real content to drive false rejections to roughly zero: a
claim's plural matches the source's singular, and a figure matches on boundaries
so a claimed "4" never grounds against a source "64". Pure — no LLM, no I/O.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from domains.wiki.claims import SourceClaim, parse_claims_doc
from domains.wiki.units import build_citable_units

# Numbers and quoted spans — matched exactly. The left guard drops a digit run
# glued to a letter or a decimal point, so "v0.1" and "IPv6" yield no token
# rather than a misleading fragment; a hyphenated name like "GPT-4" still yields
# the bare "4", which is weak but permissive, never falsely rejecting.
# A token must END on a digit: without that, "$8.7B" tokenised to "8." (the
# period swallowed, the "B" cutting the run short) and could never match the
# very unit it was copied from.
_HARD_RE = re.compile(r'"[^"]+"|(?<![A-Za-z.])\b(?:\d[\d,.]*\d|\d)')
_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*")

# Capitalised function words that are not proper nouns — they are capitalised
# only because they open a sentence or clause. Without this filter, capitals like
# "They" / "This" / "Two" are demanded of the source and reject honest
# paraphrases; newsletter-assistant measured 43% false rejection without it.
_CAP_STOP = frozenset(
    "The They It This That These Those Two Each An A In Their There He She We You "
    "Both Some All One First Second Third Its His Her Our When While If As For".split()
)

# The ceiling the claims prompt sets on how many units one claim may cite. Not
# enforced — the parser records what the model emitted — but a claim above it is
# counted, because a wide citation inflates the anchor rate without pointing better.
MAX_CITED_UNITS = 3

CitationStatus = Literal["grounded", "unchecked", "uncited", "dangling", "unsupported"]

# The verdicts that are an objection. "unchecked" is not one: the claim carried
# no number and no name, so the check had nothing to grip, which is a gap in
# coverage rather than a fault in the claim.
FAILING_STATUSES = frozenset({"uncited", "dangling", "unsupported"})


@dataclass(frozen=True)
class ClaimCheck:
    """One claim's verdict on two independent axes.

    `status` asks whether the claim's specifics exist in the SOURCE at all —
    `unsupported` means a figure or name the source never contains, which is
    fabrication. `unchecked` means the claim carried no number and no proper
    noun, so there was nothing to match; it passes vacuously and must not be
    read as evidence the check found it sound.

    `anchored` asks the narrower question of whether those specifics appear
    somewhere in the union of the units the claim cited; `anchorable` is false
    when the claim carried nothing to look for, which keeps it out of the rate.
    `over_cap` flags a claim citing more units than the prompt allows.

    The two axes are separated because a source that says "we" forces a
    self-contained claim to name its subject from elsewhere in the document: the
    pointer is imprecise, the claim is not false. Measured over six real sources,
    80% of span misses were of exactly that kind.

    `anchored` is NOT citation precision, and must not be reported as it. It asks
    only whether the anchors occur anywhere in the union of cited units, so it
    rises monotonically as a claim cites more of them — a claim citing the whole
    document scores a perfect one. It is interpretable only alongside `over_cap`:
    while claims routinely cite far more units than the cap allows (observed mean
    7.5, one claim citing 33 against a cap of 3), the rate reflects how wide the
    citations are as much as how well they point."""

    claim: SourceClaim
    status: CitationStatus
    anchored: bool
    anchorable: bool
    over_cap: bool


def _hard_tokens(text: str) -> list[str]:
    return [t.strip('"').strip() for t in _HARD_RE.findall(text)]


def _entity_words(text: str) -> list[str]:
    """The capitalised words a claim carries, minus the ones capitalised only by
    sentence position. Used for localisation only, never to judge a claim: see
    the module docstring on why capitalisation cannot identify a name."""
    words = text.split()
    initial = words[0].strip(".,;:") if words else ""
    return [
        word
        for run in _ENTITY_RE.findall(text)
        for word in run.split()
        if word not in _CAP_STOP and word != initial
    ]


def _hard_present(cited: str, token: str) -> bool:
    """Boundary-aware match, not a substring: a claimed "4" must not ground
    against source "64" (left guard) or "45" (right guard) or "8.5" (the decimal
    guard). A trailing sentence period or list comma is still allowed, so a
    figure ending a clause matches.

    The right guard rejects a following DIGIT, not any word character: letters
    after a figure are a unit, not more figure, and "$8.7B" must ground against
    "$8.7B"."""
    return re.search(rf"(?<![\w.,]){re.escape(token)}(?!\d)(?![.,]\d)", cited) is not None


def _word_present(cited: str, word: str) -> bool:
    """Word-boundary match, tolerant of singular/plural drift: a claim's "SDKs"
    matches a source's "SDK". Tolerance only ever forgives — a fabricated name
    has no source form to fold onto."""
    stem = word[:-1] if word.endswith("s") and len(word) > 1 else word
    return re.search(rf"\b{re.escape(stem)}s?\b", cited) is not None


def _normalise(text: str) -> str:
    """Lowercase and strip markdown emphasis before matching. Source bodies wrap
    identifiers and versions in italics ("_LLMSchemaCompareOperator_"), and an
    underscore glued to a token defeats the word boundary — a real false
    rejection observed upstream."""
    return re.sub(r"[_*]", " ", text).lower()


def check_citations(claims: list[SourceClaim], units: list[str]) -> list[ClaimCheck]:
    """Check each claim against the source and against the units it cites."""
    body = _normalise("\n".join(units))
    return [ClaimCheck(claim=claim, **_verdict(claim, units, body)) for claim in claims]


def _present(text: str, hard: list[str], entities: list[str]) -> bool:
    return all(_hard_present(text, t.lower()) for t in hard) and all(
        _word_present(text, w.lower()) for w in entities
    )


def _verdict(claim: SourceClaim, units: list[str], body: str) -> dict:
    if not claim.cited_units:
        return {"status": "uncited", "anchored": False, "anchorable": False, "over_cap": False}
    if any(i < 0 or i >= len(units) for i in claim.cited_units):
        return {"status": "dangling", "anchored": False, "anchorable": False, "over_cap": False}
    hard = _hard_tokens(claim.text)
    entities = _entity_words(claim.text)
    cited = _normalise(" ".join(units[i] for i in claim.cited_units))
    anchorable = bool(hard or entities)
    verdict = {
        "anchored": anchorable and _present(cited, hard, entities),
        "anchorable": anchorable,
        "over_cap": len(claim.cited_units) > MAX_CITED_UNITS,
    }
    if not hard:
        # Nothing this tier can judge — capitalised words are not evidence of a
        # name, so a claim without a figure or a quote passes vacuously.
        return {"status": "unchecked", **verdict}
    return {"status": "grounded" if _present(body, hard, []) else "unsupported", **verdict}


@dataclass(frozen=True)
class CitationSummary:
    """Per-source counts of how a claim set held up against its citations.

    `unchecked` is reported separately from `grounded` on purpose: those claims
    carried nothing matchable, so counting them as grounded would report a
    confidence the check never earned.

    `anchored` counts the claims whose specifics were found in the units they
    cited rather than elsewhere in the source, out of the `anchorable` ones that
    carried any specifics at all. Read it only next to `over_cap`, the number of
    claims citing more units than the prompt allows: both are pointer-quality
    metrics to improve, never faults to alarm on — see ClaimCheck."""

    total: int
    grounded: int
    anchorable: int
    anchored: int
    over_cap: int
    unchecked: int
    uncited: int
    dangling: int
    unsupported: int
    failing_examples: list[str]

    @property
    def failing(self) -> int:
        """Claims the check actively objected to — a fabricated specific, a
        missing citation, or a pointer to a unit that does not exist."""
        return self.uncited + self.dangling + self.unsupported


def summarise_citations(claims_doc: str, body: str, *, max_examples: int = 3) -> CitationSummary:
    """Check a stored claim doc against the source body it was extracted from.

    Rebuilds the units from the body, which is sound because a fetched body is
    written once and never re-fetched: the units the extractor cited are the
    units this reproduces."""
    checks = check_citations(parse_claims_doc(claims_doc).claims, build_citable_units(body))
    counts = Counter(c.status for c in checks)
    return CitationSummary(
        total=len(checks),
        grounded=counts["grounded"],
        anchorable=sum(1 for c in checks if c.anchorable),
        anchored=sum(1 for c in checks if c.anchored),
        over_cap=sum(1 for c in checks if c.over_cap),
        unchecked=counts["unchecked"],
        uncited=counts["uncited"],
        dangling=counts["dangling"],
        unsupported=counts["unsupported"],
        failing_examples=[
            f"[{c.status}] {c.claim.text}" for c in checks if c.status in FAILING_STATUSES
        ][:max_examples],
    )
