"""``wiki-dedup-candidates`` console script — propose near-duplicate wiki entities
for the curated dedup session (#15).

Reads entities + their top claim texts from wiki.db, embeds `name + claims`
(OpenAI), and prints the high-cosine pairs as JSON — the input to the CLUSTER →
JUDGE → CONFIRM → MERGE loop (the human gates each merge with `wiki-merge`).
Nothing here mutates state; it only reads and proposes.

Local-first: point it at the laptop copy (or a pulled-down prod snapshot) to
rehearse before prod.

    uv run wiki-dedup-candidates --db data/wiki.db \\
      --embedding-model text-embedding-3-small --embedding-dims 1536 \\
      --top-n 5 --threshold 0.8 > candidates.json

The wiki.db reader lives in `domains.wiki.dedup` (dep-free); the numpy-vectorised
pairwise-cosine search + the OpenAI wiring live here (so `domains` — the ML-dep-free
foundation — carries neither numpy nor an embedding client).
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
from domains.wiki.dedup import CandidatePair, EntityText, load_entity_texts
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
    entities). High-recall by design; the human gates the proposals downstream."""
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
    """Load entities + their top claim texts from wiki.db, embed, and return the
    near-dup pairs (cosine >= threshold), strongest first. Read-only."""
    with connection(db_path) as conn:
        items = load_entity_texts(conn, top_n=top_n)
    return find_merge_candidates(items, embed_batch, threshold=threshold)


def pairs_to_json(pairs: list[CandidatePair]) -> str:
    """Serialise the proposals for a judging session — both sides' id / name /
    text plus the score, so a reviewer (human or LLM) can decide keep/drop."""
    return json.dumps(
        [
            {
                "score": p.score,
                "a": {"entity_id": p.a.entity_id, "name": p.a.canonical_name, "text": p.a.text},
                "b": {"entity_id": p.b.entity_id, "name": p.b.canonical_name, "text": p.b.text},
            }
            for p in pairs
        ],
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from retrievers.embedding import OpenAIEmbedder

    embedder = OpenAIEmbedder(args.embedding_model, args.embedding_dims)
    pairs = run_candidates(
        args.db, embedder.embed_batch, top_n=args.top_n, threshold=args.threshold
    )

    print(pairs_to_json(pairs))
    print(
        f"{len(pairs)} candidate pair(s) at cosine >= {args.threshold} "
        f"(model={args.embedding_model} dims={args.embedding_dims} top_n={args.top_n})",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-dedup-candidates",
        description="Propose near-duplicate wiki entities for the curated merge session.",
    )
    parser.add_argument("--db", type=Path, required=True, help="path to wiki.db")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--embedding-dims", type=int, default=1536)
    parser.add_argument(
        "--top-n", type=int, default=5, help="claim texts per entity to embed (default 5)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="minimum cosine similarity to surface a pair (default 0.8)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
