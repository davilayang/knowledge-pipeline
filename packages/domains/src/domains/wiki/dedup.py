"""Merge-candidate data + the wiki.db reader for the curated dedup loop (#15).

`load_entity_texts` builds the `EntityText` list (name + top claim texts) the
candidate search embeds. The numeric pairwise-cosine search itself lives in
`evals.wiki_dedup` (it needs numpy, which `domains` — the ML-dep-free foundation —
must not carry). This module holds only the pure sqlite reader + the two records
so the search in `evals` can type against them.
"""

import sqlite3
from dataclasses import dataclass

from domains.wiki.attributed import attributed_claims_for_entity
from domains.wiki.state import get_all_entities


@dataclass(frozen=True)
class EntityText:
    """One entity's identity + the text the candidate search embeds (its name
    plus its top claim texts — the claim-centric stand-in for a summary)."""

    entity_id: str
    canonical_name: str
    text: str


@dataclass(frozen=True)
class CandidatePair:
    """A proposed near-duplicate pair, strongest first. The human judges; nothing
    here merges anything."""

    a: EntityText
    b: EntityText
    score: float


def load_entity_texts(conn: sqlite3.Connection, *, top_n: int = 5) -> list[EntityText]:
    """Build the `EntityText` list from wiki.db: every entity with its canonical
    name and its top-`top_n` claim texts joined (the claim-centric embed source —
    there is no per-entity summary column). An entity with no claims still yields
    a candidate on its name alone (empty text)."""
    out: list[EntityText] = []
    for ent in get_all_entities(conn):
        claims = attributed_claims_for_entity(conn, ent.entity_id)
        text = "\n".join(c.text for c in claims[:top_n])
        out.append(
            EntityText(entity_id=ent.entity_id, canonical_name=ent.canonical_name, text=text)
        )
    return out
