"""Surface-form mention counting — how often an entity is named in text.

Extracted from the retired raw-path salience gate: this word-boundary,
case-insensitive counter is the one piece the attributed lane still uses — the
deterministic hint behind `entity_assignment.match_claim` (which claims name
which entity). Pure + dependency-free (regex over the text).
"""

import re
from collections.abc import Sequence


def _surface_pattern(name: str, aliases: Sequence[str]) -> re.Pattern[str]:
    """Word-boundary, case-insensitive alternation over the entity's surface forms
    (canonical + aliases). Word-level, not substring — "cat" never hits
    "category". Longest form first so an alias that contains a shorter one wins."""
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
