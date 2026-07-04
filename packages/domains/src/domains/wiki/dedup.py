"""Offline merge-candidate generator for the curated dedup loop (#15).

The in-synthesis fuzzy matcher (difflib) structurally can't catch entities whose
NAMES diverge but whose MEANING converges — e.g. `Claude Max` vs `Max plan`
(difflib ratio 0.333). This module embeds `name + top claim texts` and surfaces
pairs whose cosine similarity is high, as input to the CLUSTER → JUDGE → CONFIRM →
MERGE session (the human gates every merge; this only proposes).

`find_merge_candidates` is pure over an injected `embed_batch` callable
(`list[str] -> list[list[float]]`) — so `domains` stays free of any embedding
dependency and the algorithm is testable with a fake. The OpenAI wiring lives in
the `wiki-dedup-candidates` CLI (in `evals`, which may depend on `retrievers`).
"""

import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from domains.wiki.attributed import attributed_claims_for_entity
from domains.wiki.state import get_all_entities

EmbedBatch = Callable[[list[str]], list[list[float]]]


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


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def find_merge_candidates(
    items: list[EntityText],
    embed_batch: EmbedBatch,
    *,
    threshold: float = 0.8,
) -> list[CandidatePair]:
    """Embed `name + "\\n" + text` for each entity, then return every pair with
    cosine similarity >= `threshold`, ranked `score DESC` (ties by entity_id for
    determinism). High-recall by design — the human gates the proposals."""
    if len(items) < 2:
        return []

    texts = [f"{it.canonical_name}\n{it.text}" for it in items]
    normed = [_normalize(v) for v in embed_batch(texts)]

    pairs: list[CandidatePair] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            score = sum(a * b for a, b in zip(normed[i], normed[j], strict=True))
            if score >= threshold:
                pairs.append(CandidatePair(a=items[i], b=items[j], score=score))

    pairs.sort(key=lambda p: (-p.score, p.a.entity_id, p.b.entity_id))
    return pairs


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
