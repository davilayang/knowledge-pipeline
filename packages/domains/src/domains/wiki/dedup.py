"""Merge-candidate data + the wiki.db reader for the curated dedup loop (#15).

`load_entity_texts` builds the `EntityText` list (name + top claim texts) the
candidate search embeds. The numeric pairwise-cosine search itself lives in
`evals.wiki_dedup` (it needs numpy, which `domains` — the ML-dep-free foundation —
must not carry). This module holds only the pure sqlite reader + the two records
so the search in `evals` can type against them.
"""

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from domains.wiki.attributed import attributed_claims_for_entity
from domains.wiki.state import get_all_entities

_WORD = re.compile(r"[a-z0-9]+")


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


def find_name_candidates(
    items: list[EntityText],
    *,
    threshold: float = 0.7,
    max_block: int = 400,
) -> list[CandidatePair]:
    """Lexical near-duplicate pass over CANONICAL NAMES only, ignoring claim text.

    Complements the embedding search (`evals.wiki_dedup.find_merge_candidates`),
    which embeds name + claim texts and so is claim-weighted: a claim-rich entity
    and its claim-thin duplicate embed far apart and never cross the cosine
    threshold (an 18-claim 'Agent harness' never pairs with a 0-claim 'Agentic
    harness'). Keying on the name alone recovers exactly that rich-vs-thin case.

    Score is `difflib` ratio over normalized names; pairs >= threshold are
    returned strongest first. Candidates are blocked on shared name tokens to stay
    sub-quadratic — a token appearing in more than `max_block` entities is too
    common to be a useful block key and is skipped (ponytail: block-size² ceiling;
    a pair sharing only such a token is not compared)."""
    normed = {it.entity_id: " ".join(_WORD.findall(it.canonical_name.lower())) for it in items}
    by_id = {it.entity_id: it for it in items}

    token_index: dict[str, list[str]] = {}
    for eid, name in normed.items():
        for tok in set(name.split()):
            token_index.setdefault(tok, []).append(eid)

    seen: set[tuple[str, str]] = set()
    pairs: list[CandidatePair] = []
    for ids in token_index.values():
        if len(ids) > max_block:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = tuple(sorted((ids[i], ids[j])))
                if key in seen:
                    continue
                seen.add(key)
                score = SequenceMatcher(None, normed[key[0]], normed[key[1]]).ratio()
                if score >= threshold:
                    pairs.append(CandidatePair(a=by_id[key[0]], b=by_id[key[1]], score=score))

    pairs.sort(key=lambda p: (-p.score, p.a.entity_id, p.b.entity_id))
    return pairs
