"""One-shot: rebuild `wiki.db` from `queue.db`'s already-recorded extraction docs.

After a clean-slate `wiki.db` wipe (delete + redeploy), every fetched queue row
still holds its `extract_claims` + `extract_entities` outputs in `queue.db`. This
re-persists every such row into a fresh `wiki.db` (no re-fetch) and re-renders the
entity pages. Rows that predate the attributed lane (no docs) can't be rebuilt and
are listed as needing a fresh extract pass through the Dagster DAG.

Run on the Hetzner host, in the wiki-write-quiet window the clean-slate deploy
already requires (nothing else writing `wiki.db` — this is not serialized on
WIKI_WRITE_POOL):

    ssh hcloud
    cd /home/deploy/knowledge-pipeline
    uv run python scripts/backfill_wiki_from_queue.py            # dry-run: prints the plan
    uv run python scripts/backfill_wiki_from_queue.py --apply    # persists + renders

Default is DRY-RUN: reads `queue.db`, prints how many sources would persist and
which page_ids need re-extraction, writes nothing. `--apply` costs one
subject-attribution LLM call per persisted source (same as the live persist asset)
and is idempotent (`synthesize_source` UPSERTs on content_key).
"""

import argparse
import sqlite3
from pathlib import Path

from domains.queue_store.sources import get_all_candidates, get_all_claims, get_row
from domains.wiki.state import create_schema
from workflows.wiki_synthesis.attributed_synthesis import (
    build_source_record,
    render_entity_pages,
    synthesize_source,
)

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE_DB = _ROOT / "data" / "queue.db"
DEFAULT_WIKI_DB = _ROOT / "data" / "wiki.db"
DEFAULT_WIKI_DIR = _ROOT / "data" / "wiki"


def _fetched_ids(queue_db: Path) -> list[str]:
    """page_ids with a fetched body — sorted."""
    with sqlite3.connect(queue_db) as conn:
        rows = conn.execute(
            "SELECT notion_page_id FROM queue_items "
            "WHERE raw_content IS NOT NULL AND raw_content != '' ORDER BY notion_page_id"
        ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-db", type=Path, default=DEFAULT_QUEUE_DB)
    parser.add_argument("--wiki-db", type=Path, default=DEFAULT_WIKI_DB)
    parser.add_argument("--wiki-dir", type=Path, default=DEFAULT_WIKI_DIR)
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry-run.")
    args = parser.parse_args()

    claims = dict(get_all_claims(db_path=args.queue_db))
    candidates = dict(get_all_candidates(db_path=args.queue_db))
    both = claims.keys() & candidates.keys()
    ready = [pid for pid in _fetched_ids(args.queue_db) if pid in both]
    needs_extraction = [pid for pid in _fetched_ids(args.queue_db) if pid not in both]

    print(f"{len(ready)} source(s) re-persistable; {len(needs_extraction)} need re-extraction.")
    for pid in needs_extraction:
        print(f"  needs extract: {pid}")

    if not args.apply:
        print("DRY-RUN — re-run with --apply to write wiki.db.")
        return

    create_schema(db_path=args.wiki_db)
    for pid in ready:
        synthesize_source(
            claims_doc=claims[pid],
            candidates_doc=candidates[pid],
            source=build_source_record(get_row(db_path=args.queue_db, notion_page_id=pid)),
            wiki_db_path=args.wiki_db,
        )
    written = render_entity_pages(wiki_db_path=args.wiki_db, wiki_dir=args.wiki_dir)
    print(f"Persisted {len(ready)} source(s), rendered {len(written)} page(s).")


if __name__ == "__main__":
    main()
