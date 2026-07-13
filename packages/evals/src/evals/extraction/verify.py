"""Layer-1 faithfulness check for cite-by-index claims.

Ported in-behaviour from newsletter-assistant's
`services/agent/src/agent/tools/grounding.py` (token-match verifier). A claim is
grounded iff every hard token (numbers / quoted spans, boundary-aware) and every
entity word (capitalized, per-word, function-word-filtered) in the claim text is
present in its concatenated cited units. Tokenless claims auto-pass — semantic
entailment (Layer 2) is deferred, mirroring NA.

Deterministic, code-only, no LLM. Kept aligned with NA (copied, not imported,
until the shared unit provider lands) so the two verifiers can merge later.
"""

import re

from evals.extraction.wide import Claim

# Hard tokens (numbers + quoted spans): exact, boundary-aware. Entity words
# (capitalized): matched per-word so paraphrase survives.
_HARD_RE = re.compile(r'"[^"]+"|\b\d[\d,.]*\b')
_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*")

# Capitalized function words that are NOT entities — they appear capitalized only
# because a claim is a sentence and they start it (or a clause). Without this
# filter, sentence-initial capitals ("They", "Search", "Two"…) false-reject real
# conceptual claims. Filtering these + the claim's own first word collapses
# false-reject to ~0 with no loss of true catch (NA POC 2026-07-12). STOPGAP:
# narrow to multi-word proper nouns only if false-rejects creep back.
_CAP_STOP = frozenset(
    {
        "The",
        "They",
        "It",
        "This",
        "That",
        "These",
        "Those",
        "Two",
        "Each",
        "An",
        "A",
        "In",
        "Their",
        "There",
        "He",
        "She",
        "We",
        "You",
        "Search",
        "Connections",
        "State",
        "Both",
        "Some",
        "All",
        "One",
        "First",
        "Second",
        "Third",
        "Its",
        "His",
        "Her",
        "Our",
        "When",
        "While",
        "If",
        "As",
        "For",
    }
)


def _hard_tokens(text: str) -> list[str]:
    return [t.strip('"').strip() for t in _HARD_RE.findall(text)]


def _hard_present(cited: str, tok: str) -> bool:
    # Boundary-aware, NOT plain substring: claimed "4" must not match inside "64"
    # or "0.45". Guard both sides against adjacent word chars, digits, dots, commas.
    return re.search(rf"(?<![\w.,]){re.escape(tok)}(?![\w.,])", cited) is not None


def _word_present(cited: str, word: str) -> bool:
    # Word-boundary match: entity "Ada" must not match inside "Adaptive".
    return re.search(rf"\b{re.escape(word)}\b", cited) is not None


def _entity_words(text: str) -> list[str]:
    words = text.split()
    initial = words[0] if words else ""
    out: list[str] = []
    for run in _ENTITY_RE.findall(text):
        for w in run.split():  # per-word, not the whole run
            if w in _CAP_STOP or w == initial:
                continue  # sentence-initial / function-word capital, not an entity
            out.append(w)
    return out


def verify_grounding(claims: list[Claim], units: list[str]) -> tuple[list[Claim], list[Claim]]:
    """Split claims into (grounded, ungrounded) by Layer-1 token presence in
    their cited units. Uncited or dangling-pointer claims are ungrounded."""
    grounded: list[Claim] = []
    ungrounded: list[Claim] = []
    for claim in claims:
        if not claim.cited_indices:
            ungrounded.append(claim)  # uncited — nothing to ground against
            continue
        if any(i < 0 or i >= len(units) for i in claim.cited_indices):
            ungrounded.append(claim)  # dangling pointer
            continue
        cited = " ".join(units[i] for i in claim.cited_indices).lower()
        hard_ok = all(_hard_present(cited, t.lower()) for t in _hard_tokens(claim.text))
        ent_ok = all(_word_present(cited, w.lower()) for w in _entity_words(claim.text))
        if hard_ok and ent_ok:
            grounded.append(claim)  # incl. tokenless claims (Layer 2 deferred)
        else:
            ungrounded.append(claim)
    return grounded, ungrounded
