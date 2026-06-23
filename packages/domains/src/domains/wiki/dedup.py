"""Offline merge-candidate generator for the curated dedup loop (#15).

The in-synthesis fuzzy matcher (difflib, threshold 0.85) structurally can't catch
entities whose NAMES diverge but whose MEANING converges — e.g. `Claude Max` vs
`Max plan` (ratio 0.333). This module embeds `name + summary` and surfaces pairs
whose cosine similarity is high, as input to the cluster -> judge -> confirm ->
merge session (the human gates every merge; this only proposes).

`find_merge_candidates` is pure over an injected `embed_batch` callable
(`list[str] -> list[list[float]]`) — so `domains` stays free of any embedding
dependency and the algorithm is testable with a fake. The OpenAI wiring lives in
the `wiki-dedup-candidates` CLI (in `evals`, which may depend on `retrievers`).
"""

import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from domains.wiki.io import read_meta
from domains.wiki.state import get_all_entities, get_page

EmbedBatch = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class EntityText:
    """One entity's identity + the summary text the candidate search embeds."""

    entity_id: str
    canonical_name: str
    summary: str


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
    """Embed `name + "\\n" + summary` for each entity, then return every pair with
    cosine similarity >= `threshold`, ranked `score DESC` (ties by entity_id for
    determinism). High-recall by design — the human gates the proposals."""
    if len(items) < 2:
        return []

    texts = [f"{it.canonical_name}\n{it.summary}" for it in items]
    normed = [_normalize(v) for v in embed_batch(texts)]

    pairs: list[CandidatePair] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            score = sum(a * b for a, b in zip(normed[i], normed[j], strict=True))
            if score >= threshold:
                pairs.append(CandidatePair(a=items[i], b=items[j], score=score))

    pairs.sort(key=lambda p: (-p.score, p.a.entity_id, p.b.entity_id))
    return pairs


def load_entity_texts(conn: sqlite3.Connection, wiki_dir: Path) -> list[EntityText]:
    """Build the `EntityText` list from wiki.db + the on-disk pages: every entity
    with its canonical name and the `summary` from its `.md` frontmatter. Entities
    whose page file is missing/unreadable fall back to an empty summary (still a
    candidate on the name alone)."""
    out: list[EntityText] = []
    for ent in get_all_entities(conn):
        summary = ""
        page = get_page(conn, ent.entity_id)
        if page is not None:
            path = wiki_dir / page.file_path
            try:
                summary = str(read_meta(path).get("summary", "") or "")
            except (OSError, ValueError):
                summary = ""
        out.append(
            EntityText(
                entity_id=ent.entity_id,
                canonical_name=ent.canonical_name,
                summary=summary,
            )
        )
    return out
