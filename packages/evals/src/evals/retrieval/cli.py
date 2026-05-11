"""``eval-retrieval`` console script.

Drives the retrieval eval harness end-to-end:

    uv run eval-retrieval \\
      --embedding-model text-embedding-3-small --embedding-dims 1536 \\
      --chunker-raw-store markdown --chunker-notes markdown \\
      --chunker-sessions turn_grouping --chunker-research markdown \\
      --chroma-host localhost --chroma-port 8000 \\
      --raw-store-db ${BACKUP_SOURCE_DIR}/raw_store.db \\
      --sessions-db ${BACKUP_SOURCE_DIR}/sessions.db \\
      --research-db ${BACKUP_SOURCE_DIR}/research.db \\
      --notes-dir   ${BACKUP_SOURCE_DIR}/notes

Sources without a path argument are skipped — handy for partial eval runs
while iterating on chunker config for a single source.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from domains.types import IngestItem

from .cache import CachedEmbedder
from .dataset import load_eval_set
from .embedder import OpenAIEmbedder
from .runner import run_eval
from .types import EvalConfig, EvalRunResult


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not any([args.raw_store_db, args.notes_dir, args.sessions_db, args.research_db]):
        raise SystemExit(
            "no source paths supplied — pass at least one of "
            "--raw-store-db / --notes-dir / --sessions-db / --research-db."
        )

    embedder = CachedEmbedder(
        OpenAIEmbedder(args.embedding_model, args.embedding_dims),
        cache_dir=args.cache_dir,
    )

    items_by_source = _load_items(args)
    eval_pairs = load_eval_set(args.eval_set)

    import chromadb

    chroma_client = chromadb.HttpClient(host=args.chroma_host, port=args.chroma_port)

    config = EvalConfig(
        embedding_model=args.embedding_model,
        embedding_dims=args.embedding_dims,
        chunker_by_source={
            "raw_store": args.chunker_raw_store,
            "notes": args.chunker_notes,
            "sessions": args.chunker_sessions,
            "research": args.chunker_research,
        },
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        item_limit=args.limit,
    )

    result = run_eval(
        config=config,
        eval_pairs=eval_pairs,
        items_by_source=items_by_source,
        embedder=embedder,
        chroma_client=chroma_client,
    )

    _print_summary(result)
    out_path = _write_result(result, results_dir=args.results_dir)
    print(f"\nWrote {out_path}")
    if not result.per_source:
        # Sources loaded zero items, or eval set didn't cover any source we
        # indexed. Either way the run produced no signal — surface as failure.
        print("error: no per-source metrics produced", file=sys.stderr)
        return 2
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="eval-retrieval")
    p.add_argument("--embedding-model", default="text-embedding-3-small")
    p.add_argument("--embedding-dims", type=int, default=1536)
    p.add_argument("--chunker-raw-store", default="markdown")
    p.add_argument("--chunker-notes", default="markdown")
    p.add_argument("--chunker-sessions", default="turn_grouping")
    p.add_argument("--chunker-research", default="markdown")
    p.add_argument("--chunk-size", type=int, default=800)
    p.add_argument("--chunk-overlap", type=int, default=100)
    p.add_argument("--chroma-host", default="localhost")
    p.add_argument("--chroma-port", type=int, default=8000)
    p.add_argument(
        "--eval-set",
        type=Path,
        default=Path("packages/evals/datasets/retrieval_eval.jsonl"),
        help="Path to JSONL eval pairs. Default resolves from repo root.",
    )
    p.add_argument("--raw-store-db", type=Path, default=None)
    p.add_argument("--sessions-db", type=Path, default=None)
    p.add_argument("--research-db", type=Path, default=None)
    p.add_argument("--notes-dir", type=Path, default=None)
    p.add_argument("--results-dir", type=Path, default=Path("data/eval_results"))
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/eval_results/.embedding_cache"),
    )
    p.add_argument("--limit", type=int, default=None, help="Max items per source.")
    return p.parse_args(argv)


def _load_items(args: argparse.Namespace) -> dict[str, list[IngestItem]]:
    items: dict[str, list[IngestItem]] = {}

    if args.raw_store_db is not None:
        from domains.wiki.sources import RawStoreSource

        items["raw_store"] = RawStoreSource(args.raw_store_db).get_items()

    if args.notes_dir is not None:
        from domains.wiki.sources import LocalFileSource

        items["notes"] = LocalFileSource(args.notes_dir).get_items()

    if args.sessions_db is not None:
        from domains.sessions.sources import SessionsSource

        items["sessions"] = SessionsSource(args.sessions_db).get_items()

    if args.research_db is not None:
        from domains.research.sources import ResearchSource

        items["research"] = ResearchSource(args.research_db).get_items()

    return items


def _write_result(result: EvalRunResult, *, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = results_dir / f"retrieval_{ts}.json"
    out.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return out


def _print_summary(result: EvalRunResult) -> None:
    print(
        f"model={result.embedding_model} dims={result.embedding_dims} "
        f"chunkers={result.chunker_by_source}"
    )
    print(f"{'source':<14} {'n_queries':>10} {'recall@5':>10} {'mrr@10':>10} {'ndcg@10':>10}")
    for m in result.per_source:
        print(
            f"{m.source:<14} {m.n_queries:>10} {m.recall_at_5:>10.3f} "
            f"{m.mrr_at_10:>10.3f} {m.ndcg_at_10:>10.3f}"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
