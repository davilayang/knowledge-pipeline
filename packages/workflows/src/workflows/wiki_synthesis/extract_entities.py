"""Attributed-lane entity extractor (Layer 1.5).

Reads the raw article AND the claims already extracted from it, returning the
named entities the article is about — the candidate set the attributed lane
resolves against the live wiki. Reading the ARTICLE (not just the claims)
recovers the article's implicit subject and long-tail entities that never appear
verbatim in the claim bullets — the recall gap when entity extraction ran over
the claims alone. Shares the article prompt-cache prefix with `extract_claims`
(both build it via `shared_prefix_messages`), so this second read of the same
article is served from OpenAI's server-side cache.

No hard entity cap: downstream salience classifies the long tail (a peripheral
one-off is minted row-only, not paged). The LLM proposes name + type only; it
never mints a surrogate id — `resolve_or_mint` against the live wiki owns
identity. Claim-extraction quality is validated empirically; the wiring below
(parsing, type normalisation, dedup) is unit-tested.
"""

import logging
from typing import get_args

from domains.types import IngestItem
from domains.wiki.claims import ClaimSet
from domains.wiki.identity import Candidate
from domains.wiki.types import PageType

from workflows.llm import LLMCall, generate_messages_with_usage
from workflows.wiki_synthesis.extract_shared import shared_prefix_messages
from workflows.wiki_synthesis.prompts import EXTRACT_ENTITIES_TASK

logger = logging.getLogger(__name__)

EXTRACT_ENTITIES_MODEL = "gpt-4.1-mini"

_VALID_PAGE_TYPES = frozenset(get_args(PageType))

# The task asks for exactly the PageType vocabulary, but the model drifts to
# near-synonyms — map the common ones back; anything unrecognised falls to
# "concept" (the modal type) rather than being dropped, so a mis-typed real
# entity still becomes a candidate (recall over tidy typing).
_TYPE_ALIASES = {
    "technology": "tool",
    "product": "tool",
    "software": "tool",
    "library": "tool",
    "framework": "concept",
    "model": "tool",
    "org": "organization",
    "organisation": "organization",
    "company": "organization",
    "group": "organization",
    "technique": "method",
    "algorithm": "method",
    "paper": "other",
    "place": "other",
    "law": "other",
}

# `Name — type` — accept em dash, en dash, or a spaced hyphen as the separator.
_SEPARATORS = (" — ", " – ", " - ")


def _normalize_type(raw: str) -> str:
    """Map the model's type token to a valid PageType. Takes the first word (so
    'tool/model' → 'tool'), lowercases, then the alias map; unknown → 'concept'."""
    token = raw.strip().strip("`").lower().replace("/", " ").split()
    if not token:
        return "concept"
    head = token[0]
    if head in _VALID_PAGE_TYPES:
        return head
    return _TYPE_ALIASES.get(head, "concept")


def _split_name_type(line: str) -> tuple[str, str]:
    """Split a `Name — type` line into (name, raw_type). Splits on the LAST
    separator (names may contain a hyphen, e.g. 'cross-encoder'); a line with no
    separator is all name with an empty type."""
    for sep in _SEPARATORS:
        if sep in line:
            name, _, type_part = line.rpartition(sep)
            return name.strip(), type_part.strip()
    return line.strip(), ""


def parse_entity_candidates(text: str) -> list[Candidate]:
    """Parse the entity task's `Name — type` output into Candidates.

    Skips blanks, a lone NONE, and bullet/number markers; dedups by lowercased
    name (first spelling wins); normalises the type to a valid PageType. The LLM
    supplies no id or aliases — resolution against the live wiki owns identity."""
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if not line or line.upper() == "NONE":
            continue
        # The format is mandatory `Name — type`; a line with no separator is prose
        # the model slipped in (e.g. "I could not find any entities"), not an entity.
        if not any(sep in line for sep in _SEPARATORS):
            continue
        name, type_part = _split_name_type(line)
        name = name.strip("`").strip()
        # A real entity name is short; a long name is almost always a prose
        # sentence that happens to contain a dash — drop it (recall of REAL
        # entities, not sentences).
        if not name or len(name) > 60:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(Candidate(name=name, page_type=_normalize_type(type_part)))
    return candidates


def extract_entities(
    item: IngestItem, claims: ClaimSet, *, model: str = EXTRACT_ENTITIES_MODEL
) -> tuple[list[Candidate], LLMCall]:
    """Extract candidate entities from the article + its extracted claims.

    `claims` is the ClaimSet from `extract_claims`; the claim texts ride in the
    task tail as a salience signal while the article (in the shared cached prefix)
    supplies the long tail. Returns (candidates, call). temperature=0 for
    reproducible candidate sets, matching the claims call."""
    claim_block = "\n".join(f"- {c.text}" for c in claims.claims) or "(no claims extracted)"
    task = EXTRACT_ENTITIES_TASK.format(claims=claim_block)
    messages = shared_prefix_messages(item, task)
    call = generate_messages_with_usage(messages, model=model, temperature=0)
    candidates = parse_entity_candidates(call.content)
    if not candidates and "NONE" not in call.content.upper():
        # No candidates and no honest NONE — the model ignored the line format.
        # Surface it so a silent empty candidate set is auditable, not invisible.
        logger.warning(
            "extract_entities parsed no candidates for %s (no NONE marker); output starts: %r",
            item.item_id,
            call.content[:200],
        )
    return candidates, call
