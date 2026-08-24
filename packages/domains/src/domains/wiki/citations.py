"""Check an extracted claim against the source units it cites.

A claim carries the indices of the units it came from (see domains.wiki.units).
This module answers, for free and without an LLM, whether the claim's hard
specifics — its numbers, its quoted spans, its proper nouns — actually appear in
those units. That catches the fabrication class worth catching: an invented
figure or a name attached to the wrong subject.

Ported from newsletter-assistant's verifier lexical baseline, whose heuristics
were tuned against real content to drive false rejections to roughly zero. The
tuning is what the odd-looking rules below are for: sentence-initial capitals
are not proper nouns, a claim's plural must match the source's singular, and a
figure must match on boundaries so a claimed "4" never grounds against a source
"64". Pure — no LLM, no I/O.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from domains.wiki.claims import SourceClaim, parse_claims_doc
from domains.wiki.units import build_citable_units

# Numbers and quoted spans — matched exactly. The `(?<![A-Za-z.])` guard drops a
# digit run glued to a letter or a decimal point, so an identifier ("v0.1",
# "GPT-4", "IPv6") yields no token at all rather than a misleading fragment.
_HARD_RE = re.compile(r'"[^"]+"|(?<![A-Za-z.])\b\d[\d,.]*\b')
_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*")

# Capitalised function words that are not proper nouns — they are capitalised
# only because they open a sentence or clause. Without this filter, capitals like
# "They" / "This" / "Two" are demanded of the source and reject honest
# paraphrases; newsletter-assistant measured 43% false rejection without it.
_CAP_STOP = frozenset(
    "The They It This That These Those Two Each An A In Their There He She We You "
    "Both Some All One First Second Third Its His Her Our When While If As For".split()
)

CitationStatus = Literal["grounded", "unchecked", "uncited", "dangling", "unsupported"]

# The verdicts that are an objection. "unchecked" is not one: the claim carried
# no number and no name, so the check had nothing to grip, which is a gap in
# coverage rather than a fault in the claim.
FAILING_STATUSES = frozenset({"uncited", "dangling", "unsupported"})


@dataclass(frozen=True)
class ClaimCheck:
    """One claim's citation verdict. `unchecked` means the claim carried no
    number and no proper noun, so there was nothing to match — it passes
    vacuously and must not be counted as evidence the check found it sound."""

    claim: SourceClaim
    status: CitationStatus


def _hard_tokens(text: str) -> list[str]:
    return [t.strip('"').strip() for t in _HARD_RE.findall(text)]


def _entity_words(text: str, body: str) -> list[str]:
    """The proper nouns a claim must find in the units it cites.

    A claim's OPENING word is capitalised whether it is a name or not, and
    extracted claims are written subject-first, so the opening word is both the
    most valuable token to check and the most ambiguous one. The source settles
    it: if the body ever uses the word lowercase, the capital was sentence
    position ("Trained on 8 GPUs" against a body saying "the team trained"), so
    skip it. If the body only ever capitalises it — or never contains it at
    all — it is a name, and the claim must find it."""
    words = text.split()
    initial = words[0].strip(".,;:") if words else ""
    out: list[str] = []
    for run in _ENTITY_RE.findall(text):
        for word in run.split():
            if word in _CAP_STOP:
                continue
            if word == initial and re.search(rf"\b{re.escape(word.lower())}\b", body):
                continue
            out.append(word)
    return out


def _hard_present(cited: str, token: str) -> bool:
    """Boundary-aware match, not a substring: a claimed "4" must not ground
    against source "64" or "0.45". The right guard still allows a trailing
    sentence period or list comma, so a figure ending a clause matches."""
    return re.search(rf"(?<![\w.,]){re.escape(token)}(?![\w])(?![.,]\d)", cited) is not None


def _word_present(cited: str, word: str) -> bool:
    """Word-boundary match, tolerant of singular/plural drift: a claim's "SDKs"
    matches a source's "SDK". Tolerance only ever forgives — a fabricated name
    has no source form to fold onto."""
    stem = word[:-1] if word.endswith("s") and len(word) > 1 else word
    return re.search(rf"\b{re.escape(stem)}s?\b", cited) is not None


def check_citations(claims: list[SourceClaim], units: list[str]) -> list[ClaimCheck]:
    """Check each claim against the units it cites, in order."""
    results: list[ClaimCheck] = []
    for claim in claims:
        results.append(ClaimCheck(claim=claim, status=_status(claim, units)))
    return results


def _status(claim: SourceClaim, units: list[str]) -> CitationStatus:
    if not claim.cited_units:
        return "uncited"
    if any(i < 0 or i >= len(units) for i in claim.cited_units):
        return "dangling"
    # Strip markdown emphasis before matching: source bodies wrap identifiers and
    # versions in italics ("_LLMSchemaCompareOperator_"), and an underscore glued
    # to a token defeats the word boundary — a real false rejection upstream.
    cited = re.sub(r"[_*]", " ", " ".join(units[i] for i in claim.cited_units)).lower()
    hard = _hard_tokens(claim.text)
    entities = _entity_words(claim.text, "\n".join(units))
    if not hard and not entities:
        return "unchecked"
    if all(_hard_present(cited, t.lower()) for t in hard) and all(
        _word_present(cited, w.lower()) for w in entities
    ):
        return "grounded"
    return "unsupported"


@dataclass(frozen=True)
class CitationSummary:
    """Per-source counts of how a claim set held up against its citations.

    `unchecked` is reported separately from `grounded` on purpose: those claims
    carried nothing matchable, so counting them as grounded would report a
    confidence the check never earned."""

    total: int
    grounded: int
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
        unchecked=counts["unchecked"],
        uncited=counts["uncited"],
        dangling=counts["dangling"],
        unsupported=counts["unsupported"],
        failing_examples=[
            f"[{c.status}] {c.claim.text}" for c in checks if c.status in FAILING_STATUSES
        ][:max_examples],
    )
