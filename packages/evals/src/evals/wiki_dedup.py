"""``wiki-dedup-candidates`` console script — propose near-duplicate wiki
entities for the curated dedup session (#15).

Reads entities + summaries from wiki.db + the on-disk pages, embeds
`name + summary` (OpenAI), and prints the high-cosine pairs as JSON — the input
to the cluster -> judge -> confirm -> merge loop (the human gates each merge with
`wiki-merge`). Nothing here mutates state; it only reads and proposes.

Local-first: point it at the laptop copy to rehearse before prod.

    uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki \\
      --embedding-model text-embedding-3-small --embedding-dims 1536 \\
      --threshold 0.8 > candidates.json

The pure search + the wiki reader live in `domains.wiki.dedup`; this module only
adds the OpenAI wiring + the operator I/O (so `domains` stays embedding-free).
"""

import argparse
import json
import sys
from pathlib import Path

from domains.wiki.dedup import CandidatePair, EmbedBatch, find_merge_candidates, load_entity_texts
from domains.wiki.state import connection


def run_candidates(
    db_path: Path | str,
    wiki_dir: Path | str,
    embed_batch: EmbedBatch,
    *,
    threshold: float = 0.8,
) -> list[CandidatePair]:
    """Load entities from wiki.db + the pages, embed, and return the near-dup
    pairs (cosine >= threshold), strongest first. Read-only."""
    with connection(db_path) as conn:
        items = load_entity_texts(conn, Path(wiki_dir))
    return find_merge_candidates(items, embed_batch, threshold=threshold)


def pairs_to_json(pairs: list[CandidatePair]) -> str:
    """Serialise the proposals for a judging session — both sides' id / name /
    summary plus the score, so a reviewer (human or LLM) can decide keep/drop."""
    return json.dumps(
        [
            {
                "score": p.score,
                "a": {
                    "entity_id": p.a.entity_id,
                    "name": p.a.canonical_name,
                    "summary": p.a.summary,
                },
                "b": {
                    "entity_id": p.b.entity_id,
                    "name": p.b.canonical_name,
                    "summary": p.b.summary,
                },
            }
            for p in pairs
        ],
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from retrievers.embedding import OpenAIEmbedder

    embedder = OpenAIEmbedder(args.embedding_model, args.embedding_dims)
    pairs = run_candidates(args.db, args.wiki_dir, embedder.embed_batch, threshold=args.threshold)

    print(pairs_to_json(pairs))
    print(
        f"{len(pairs)} candidate pair(s) at cosine >= {args.threshold} "
        f"(model={args.embedding_model} dims={args.embedding_dims})",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-dedup-candidates",
        description="Propose near-duplicate wiki entities for the curated merge session.",
    )
    parser.add_argument("--db", type=Path, required=True, help="path to wiki.db")
    parser.add_argument("--wiki-dir", type=Path, required=True, help="dir holding the .md pages")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--embedding-dims", type=int, default=1536)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="minimum cosine similarity to surface a pair (default 0.8)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
