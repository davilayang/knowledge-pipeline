# Entity alias resolution — load/save/lookup aliases.yaml with fuzzy matching.

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FUZZY_THRESHOLD = 0.85


@dataclass
class AliasEntry:
    """A single entity's canonical name and known aliases."""

    canonical: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class AliasStore:
    """In-memory alias store. Load from YAML, modify, save back."""

    entries: dict[str, AliasEntry] = field(default_factory=dict)

    def lookup(self, name: str) -> str | None:
        """Find entity_id for a name (exact match on canonical or aliases)."""
        name_lower = name.lower()
        for entity_id, entry in self.entries.items():
            if entry.canonical.lower() == name_lower:
                return entity_id
            if any(a.lower() == name_lower for a in entry.aliases):
                return entity_id
        return None

    def fuzzy_match(self, name: str) -> str | None:
        """Find entity_id via fuzzy matching against all names.

        Returns the best match above FUZZY_THRESHOLD, or None.
        """
        name_lower = name.lower()
        best_id: str | None = None
        best_score: float = 0.0

        for entity_id, entry in self.entries.items():
            all_names = [entry.canonical] + entry.aliases
            for candidate in all_names:
                score = difflib.SequenceMatcher(None, name_lower, candidate.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_id = entity_id

        if best_score >= FUZZY_THRESHOLD:
            return best_id
        return None

    def resolve(self, name: str) -> str | None:
        """Resolve a name to entity_id: exact match first, then fuzzy."""
        return self.lookup(name) or self.fuzzy_match(name)

    def add(self, entity_id: str, canonical: str, aliases: list[str] | None = None) -> None:
        """Add or update an alias entry."""
        self.entries[entity_id] = AliasEntry(canonical=canonical, aliases=aliases or [])


def load_aliases(path: Path) -> AliasStore:
    """Load aliases from a YAML file."""
    if not path.exists():
        return AliasStore()

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return AliasStore()

    entries = {}
    for entity_id, info in data.items():
        entries[entity_id] = AliasEntry(
            canonical=info.get("canonical", ""),
            aliases=info.get("aliases", []),
        )
    return AliasStore(entries=entries)


def save_aliases(path: Path, store: AliasStore) -> None:
    """Save aliases to a YAML file."""
    data = {}
    for entity_id, entry in store.entries.items():
        data[entity_id] = {
            "canonical": entry.canonical,
            "aliases": entry.aliases,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
