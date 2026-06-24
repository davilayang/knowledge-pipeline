"""``wiki-dedup-candidates`` console script — propose near-duplicate wiki
entities for the curated dedup session (#15).

Reads entities + summaries, embeds `name + summary`, and prints the high-cosine
pairs as JSON — the input to the cluster -> judge -> confirm -> merge loop (the
human gates each merge with `wiki-merge`). Read-only; never mutates state.

Read source: prod over Datasette (`--datasette-url`, no local copy) or a local
wiki.db (`--db`/`--wiki-dir`, rehearsal). Embedding backend: OpenAI by default,
or any OpenAI-compatible server via `--embed-base-url` (a local
`llama-server --embeddings` — free, no key). Candidate generation is just a
similarity heuristic a human then judges, so it needn't match the production
Chroma embedding space. Runbook: `domains/wiki/CURATION.md`.

    # prod over Tailscale + local llama.cpp embeddings (no file copy, free)
    uv run wiki-dedup-candidates \\
      --datasette-url https://<host>/databases/wiki \\
      --embed-base-url http://localhost:8080/v1 \\
      --embedding-model nomic-embed-text-v1.5 --embed-prefix "search_document: " > candidates.json

    # local rehearsal, OpenAI embeddings
    uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki > candidates.json

The pure search + the local wiki reader live in `domains.wiki.dedup`; this module
adds the Datasette reader, the embedder wiring, and the operator I/O (so
`domains` stays embedding- and HTTP-free).
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from domains.wiki.dedup import (
    CandidatePair,
    EmbedBatch,
    EntityText,
    find_merge_candidates,
    load_entity_texts,
)
from domains.wiki.state import connection

# Pull entities + the HEAD-version summary straight from wiki.db over Datasette,
# so the read needs no local file copy (summary lives in page_versions, not the
# .md frontmatter the local reader uses — same content, written by synthesis).
_DATASETTE_SQL = (
    "SELECT e.entity_id, e.canonical_name, COALESCE(pv.summary, '') AS summary "
    "FROM entities e "
    "LEFT JOIN pages p ON p.entity_id = e.entity_id "
    "LEFT JOIN page_versions pv "
    "  ON pv.entity_id = e.entity_id AND pv.version = p.current_version "
    "ORDER BY e.entity_id"
)


def fetch_entity_texts_via_datasette(
    base_url: str,
    *,
    opener: Callable = urllib.request.urlopen,
) -> list[EntityText]:
    """Read entities + summaries from a Datasette-exposed wiki.db over HTTP
    (read-only SQL via `?sql=`). `base_url` is the database URL, e.g.
    `https://<host>/databases/wiki`."""
    query = urllib.parse.urlencode({"sql": _DATASETTE_SQL, "_shape": "array"})
    with opener(f"{base_url.rstrip('/')}.json?{query}") as resp:
        rows = json.loads(resp.read())
    return [
        EntityText(
            entity_id=r["entity_id"],
            canonical_name=r["canonical_name"],
            summary=r["summary"] or "",
        )
        for r in rows
    ]


def run_candidates(
    embed_batch: EmbedBatch,
    *,
    db_path: Path | str | None = None,
    wiki_dir: Path | str | None = None,
    datasette_url: str | None = None,
    threshold: float = 0.8,
) -> list[CandidatePair]:
    """Load entities (from a local wiki.db + pages, or over Datasette), embed,
    and return near-dup pairs (cosine >= threshold), strongest first. Read-only."""
    if datasette_url:
        items = fetch_entity_texts_via_datasette(datasette_url)
    else:
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
    pairs = run_candidates(
        embed_batch,
        db_path=args.db,
        wiki_dir=args.wiki_dir,
        datasette_url=args.datasette_url,
        threshold=args.threshold,
    )

    print(pairs_to_json(pairs))
    source = args.datasette_url or f"{args.db} + {args.wiki_dir}"
    backend = args.embed_base_url or "openai"
    print(
        f"{len(pairs)} candidate pair(s) at cosine >= {args.threshold} "
        f"(source={source} backend={backend} model={args.embedding_model})",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-dedup-candidates",
        description="Propose near-duplicate wiki entities for the curated merge session.",
    )
    # Read source: prod over Datasette (no local copy), OR a local wiki.db + pages.
    parser.add_argument(
        "--datasette-url",
        default=None,
        help=(
            "read entities + summaries from a Datasette-exposed wiki.db over HTTP, "
            "e.g. https://<host>/databases/wiki — no local copy needed"
        ),
    )
    parser.add_argument("--db", type=Path, help="path to a local wiki.db (rehearsal mode)")
    parser.add_argument("--wiki-dir", type=Path, help="local dir holding the .md pages")
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
    args = parser.parse_args(argv)
    if not args.datasette_url and not (args.db and args.wiki_dir):
        parser.error("pass --datasette-url, or both --db and --wiki-dir")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
