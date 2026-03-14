# CLI entrypoint for the knowledge pipeline.
#
# Usage:
#   python -m knowledge_pipeline index-knowledge
#   python -m knowledge_pipeline index-knowledge --source medium --since 2026-03-01
#   python -m knowledge_pipeline backup
#   python -m knowledge_pipeline backup --max-backups 14

import argparse
import logging
import sys
from datetime import date


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="knowledge-pipeline",
        description="Prefect-based orchestrator for newsletter knowledge indexing and backup",
    )
    sub = parser.add_subparsers(dest="command")

    # ── index-knowledge ──────────────────────────────────────────────────
    ik = sub.add_parser(
        "index-knowledge",
        help="Copy raw_store.db and index content into ChromaDB",
    )
    ik.add_argument("--source", type=str, default=None, help="Filter by source_key")
    ik.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        help="Only index content from this date onward (YYYY-MM-DD)",
    )
    ik.add_argument(
        "--statuses",
        nargs="+",
        default=None,
        help="Vector statuses to process (default: pending ready)",
    )
    ik.add_argument(
        "--skip-copy",
        action="store_true",
        help="Skip copying raw_store.db (use existing local copy)",
    )

    # ── backup ───────────────────────────────────────────────────────────
    bk = sub.add_parser(
        "backup",
        help="Backup newsletter-assistant databases",
    )
    bk.add_argument(
        "--max-backups",
        type=int,
        default=None,
        help="Number of backup directories to retain (default: 7)",
    )
    bk.add_argument(
        "--db-files",
        nargs="+",
        default=None,
        help="Database filenames to back up (default: all configured)",
    )

    args = parser.parse_args()

    if args.command == "index-knowledge":
        from knowledge_pipeline.flows.index_knowledge import index_knowledge

        result = index_knowledge(
            source_key=args.source,
            since=args.since,
            statuses=args.statuses,
            skip_copy=args.skip_copy,
        )
        print(f"Done: {result}")

    elif args.command == "backup":
        from knowledge_pipeline.flows.backup import backup_databases

        kwargs: dict = {}
        if args.max_backups is not None:
            kwargs["max_backups"] = args.max_backups
        if args.db_files is not None:
            kwargs["db_files"] = args.db_files

        result = backup_databases(**kwargs)
        print(f"Done: {result}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
