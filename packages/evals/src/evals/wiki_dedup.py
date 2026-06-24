"""``wiki-dedup-candidates`` console script — propose near-duplicate wiki
entities for the curated dedup session (#15).

Reads entities + summaries from wiki.db + the on-disk pages, embeds
`name + summary`, and prints the high-cosine pairs as JSON — the input to the
cluster -> judge -> confirm -> merge loop (the human gates each merge with
`wiki-merge`). Nothing here mutates state; it only reads and proposes.

Embedding backend: OpenAI by default, or ANY OpenAI-compatible server via
`--embed-base-url` (e.g. a local `llama-server --embeddings` — free, no key).
Candidate generation is just a similarity heuristic a human then judges, so it
needn't match the production Chroma embedding space. See the runbook in
`domains/wiki/CURATION.md` ("Embedding backend") for the llama.cpp setup.

    # OpenAI (default)
    uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki > candidates.json

    # local llama.cpp (free)
    uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki \\
      --embed-base-url http://localhost:8080/v1 \\
      --embedding-model nomic-embed-text-v1.5 --embed-prefix "search_document: "

The pure search + the wiki reader live in `domains.wiki.dedup`; this module only
adds the embedder wiring + the operator I/O (so `domains` stays embedding-free).
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


def embed_batch_with_prefix(embed_batch: EmbedBatch, prefix: str) -> EmbedBatch:
    """Wrap an embed callable to prepend a task `prefix` to every text. Some
    local models want one (e.g. nomic-embed expects `search_document: `).
    Returns the callable unchanged when `prefix` is empty."""
    if not prefix:
        return embed_batch
    return lambda texts: embed_batch([prefix + t for t in texts])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from retrievers.embedding import OpenAIEmbedder

    # Default = OpenAI. With --embed-base-url, the SAME class drives a local
    # OpenAI-compatible server (llama.cpp `llama-server --embeddings`, Ollama,
    # vLLM): no real key needed, and dims must be None — llama.cpp's
    # /v1/embeddings rejects the `dimensions` param and uses the native dim.
    local = bool(args.embed_base_url)
    embedder = OpenAIEmbedder(
        args.embedding_model,
        dims=None if local else args.embedding_dims,
        api_key="no-key" if local else None,
        base_url=args.embed_base_url,
    )
    embed_batch = embed_batch_with_prefix(embedder.embed_batch, args.embed_prefix)
    pairs = run_candidates(args.db, args.wiki_dir, embed_batch, threshold=args.threshold)

    print(pairs_to_json(pairs))
    backend = args.embed_base_url or "openai"
    print(
        f"{len(pairs)} candidate pair(s) at cosine >= {args.threshold} "
        f"(backend={backend} model={args.embedding_model})",
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
    parser.add_argument(
        "--embedding-dims",
        type=int,
        default=1536,
        help="OpenAI Matryoshka dimension (ignored when --embed-base-url is set)",
    )
    parser.add_argument(
        "--embed-base-url",
        default=None,
        help=(
            "point at a local OpenAI-compatible embeddings server instead of OpenAI "
            "(e.g. http://localhost:8080/v1 for llama-server --embeddings)"
        ),
    )
    parser.add_argument(
        "--embed-prefix",
        default="",
        help="task prefix prepended to every text (e.g. 'search_document: ' for nomic-embed)",
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
