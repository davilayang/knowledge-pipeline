"""Merge-candidate search for the curated dedup loop (#15).

Assumes an agent (Claude Code / Codex / …) drives the loop: it calls
`openai_candidates(db_path)`, reasons over the returned `CandidatePair`s (the
JUDGE step — cluster transitively, reject version-variants/homonyms), gets human
CONFIRM, then runs `wiki-merge` per confirmed pair. No operator CLI or JSON file —
the agent consumes the pairs directly.

The wiki.db reader lives in `domains.wiki.dedup` (dep-free); the numpy pairwise-
cosine + the OpenAI wiring live here (so `domains` — the ML-dep-free foundation —
carries neither numpy nor an embedding client).
"""

from collections.abc import Callable
from pathlib import Path

import numpy as np
from domains.wiki.dedup import (
    CandidatePair,
    EntityText,
    find_name_candidates,
    load_entity_texts,
)
from domains.wiki.state import connection

EmbedBatch = Callable[[list[str]], list[list[float]]]


def find_merge_candidates(
    items: list[EntityText],
    embed_batch: EmbedBatch,
    *,
    threshold: float = 0.8,
) -> list[CandidatePair]:
    """Embed `name + "\\n" + text` for each entity, then return every pair with
    cosine similarity >= `threshold`, ranked `score DESC` (ties by entity_id for
    determinism). Vectorised with numpy — the full pairwise cosine matrix is a
    single matmul (a pure-Python O(n²)×dims loop times out at a few thousand
    entities). High-recall by design; the agent + human gate the proposals."""
    if len(items) < 2:
        return []

    texts = [f"{it.canonical_name}\n{it.text}" for it in items]
    mat = np.asarray(embed_batch(texts), dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    normed = mat / np.where(norms == 0.0, 1.0, norms)  # zero-vectors → score 0

    sims = normed @ normed.T
    ia, ib = np.triu_indices(len(items), k=1)  # upper triangle, no self-pairs
    mask = sims[ia, ib] >= threshold

    pairs = [
        CandidatePair(a=items[i], b=items[j], score=float(sims[i, j]))
        for i, j in zip(ia[mask].tolist(), ib[mask].tolist(), strict=True)
    ]
    pairs.sort(key=lambda p: (-p.score, p.a.entity_id, p.b.entity_id))
    return pairs


def run_candidates(
    db_path: Path | str,
    embed_batch: EmbedBatch,
    *,
    top_n: int = 5,
    threshold: float = 0.8,
) -> list[CandidatePair]:
    """Load entities + their top claim texts from wiki.db, embed (injected), and
    return the near-dup pairs (cosine >= threshold), strongest first. Read-only.
    Takes an injected embedder so it's testable with a fake."""
    with connection(db_path) as conn:
        items = load_entity_texts(conn, top_n=top_n)
    return find_merge_candidates(items, embed_batch, threshold=threshold)


def _union(*groups: list[CandidatePair]) -> list[CandidatePair]:
    """Merge candidate lists, keeping one pair per entity-pair (highest score),
    strongest first. Lets the embedding + lexical passes overlap without dupes."""
    best: dict[frozenset[str], CandidatePair] = {}
    for group in groups:
        for p in group:
            key = frozenset((p.a.entity_id, p.b.entity_id))
            if key not in best or p.score > best[key].score:
                best[key] = p
    return sorted(best.values(), key=lambda p: (-p.score, p.a.entity_id, p.b.entity_id))


def openai_candidates(
    db_path: Path | str,
    *,
    top_n: int = 5,
    threshold: float = 0.8,
    name_threshold: float = 0.8,
    model: str = "text-embedding-3-small",
    dims: int = 1536,
) -> list[CandidatePair]:
    """The agent's entry point: return near-dup pairs, strongest first, from TWO
    complementary passes unioned — the embedding search (claim-weighted, catches
    semantic dups) and the lexical name search (claim-blind, catches a claim-rich
    entity's thin name-twin the embedding pass misses). Read-only — run in-cluster
    (docker exec) or on a pulled copy. `OPENAI_API_KEY` must be set."""
    from retrievers.embedding import OpenAIEmbedder

    embedder = OpenAIEmbedder(model, dims)
    with connection(db_path) as conn:
        items = load_entity_texts(conn, top_n=top_n)
    embedded = find_merge_candidates(items, embedder.embed_batch, threshold=threshold)
    named = find_name_candidates(items, threshold=name_threshold)
    return _union(embedded, named)
