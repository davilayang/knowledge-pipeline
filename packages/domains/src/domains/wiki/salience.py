"""Deterministic source-salience gate for wiki page synthesis.

Page synthesis attaches a source to an entity on MERE MENTION: if an article
names entity E once, an edge `page_sources(E, article)` is created and the WHOLE
article drives E's page. That conflates "mentions E" with "is about E" — pages
absorb off-topic content (measured: 53% of 249 prod page_source edges were
low-salience; a one-mention "YOYO" article polluted both Anthropic and Claude
Code). Faithfulness is fine — the content IS in the source — so this is a
relevance failure the faithfulness/specificity judges miss.

This module decides, from cheap deterministic text features, whether an entity
is salient enough to THIS article to drive its page. Pure + dependency-free
(regex over the article text + entity surface forms), mirroring `relevance.py`:
no LLM. Web research (Asgarieh 2024; GUM-SAGE 2025) is clear that LLM
self-grading of salience is weak as a standalone gate, so the deterministic
floor decides; an optional LLM grade may be layered on later as one more signal
but never overrides a clear peripheral.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

# Characters of the article counted as the "lead" — title + opening, where a
# document's real subjects are introduced (first-mention-position literature).
LEAD_CHARS = 600

# A source attaches to the entity's page when the entity is named at least this
# many times OR appears in the title. Tunable; calibrated on hand-labelled edges.
MENTION_FLOOR = 3


def _surface_pattern(name: str, aliases: Sequence[str]) -> re.Pattern[str]:
    """Word-boundary, case-insensitive alternation over the entity's surface forms
    (canonical + aliases). Word-level, not substring — "cat" never hits
    "category" (mirrors relevance.py)."""
    forms = [f for f in (name, *aliases) if f.strip()]
    alt = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alt})\b", re.IGNORECASE)


def count_mentions(name: str, aliases: Sequence[str], text: str) -> int:
    """How many times the entity (by canonical name or any alias) is named in
    `text` — case-insensitive, word-boundary. A surface-string count; coref-aware
    counting (pronouns) is a later upgrade."""
    if not name.strip():
        return 0
    return len(_surface_pattern(name, aliases).findall(text))


@dataclass(frozen=True)
class SalienceFeatures:
    """Deterministic centrality signals for one (entity, article) edge."""

    mention_count: int  # body mentions of canonical + aliases
    in_title: bool  # surface form appears in the article title
    in_lead: bool  # surface form appears in the first LEAD_CHARS of the body
    first_mention_ratio: float | None  # offset of first body mention / len(text); None if absent


def salience_features(
    *, name: str, aliases: Sequence[str], title: str, text: str, lead_chars: int = LEAD_CHARS
) -> SalienceFeatures:
    """Compute the deterministic salience features of an entity within an article.

    Mentions are counted over the body `text` only; title presence is its own
    high-signal feature (first-mention-position literature treats headline
    presence separately). `first_mention_ratio` is where in the body the entity
    first appears — early ≈ central, late ≈ peripheral."""
    pattern = _surface_pattern(name, aliases) if name.strip() else None
    body_hits = pattern.findall(text) if pattern else []
    first = pattern.search(text) if pattern else None
    return SalienceFeatures(
        mention_count=len(body_hits),
        in_title=bool(pattern.search(title)) if pattern else False,
        in_lead=bool(pattern.search(text[:lead_chars])) if pattern else False,
        first_mention_ratio=(first.start() / len(text)) if first and text else None,
    )


def is_salient(features: SalienceFeatures, *, mention_floor: int = MENTION_FLOOR) -> bool:
    """The deterministic gate: does this source attach to the entity's page?

    A source is salient when the entity is in the title OR named at least
    `mention_floor` times in the body. Below that, with no title hit, it's a
    peripheral mention (the one-mention "YOYO" case) — kept for the graph, not
    the page. `in_lead` / `first_mention_ratio` are retained on the features for
    calibration + auditing; the v1 binary decision is the deterministic floor
    (an optional LLM grade may be added later but never overrides this floor)."""
    return features.in_title or features.mention_count >= mention_floor
