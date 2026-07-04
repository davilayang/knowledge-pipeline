"""One-shot: run the attributed-lane extraction (`extract_claims` +
`extract_entities`) over `queue.db` rows that were fetched *before* those assets
existed, so they gain the two `extraction_calls` docs the wiki sweep needs.

The `fetch_extract_queue` Dagster assets only run for rows the Notion sensor
sees at Status=Fetching. Rows fetched under an older deploy are past that status
and their dynamic partitions aren't registered, so the DAG can never re-process
them. This script bypasses Dagster: it reads the cached body straight from
`queue.db` and calls the SAME `extract_claims` / `extract_entities` functions the
assets call, recording identical `extraction_calls` rows. No re-fetch, no LLM
topic-card work, no Notion writes.

Run on the Hetzner host:

    ssh hcloud
    cd /home/deploy/knowledge-pipeline
    ~/.local/bin/uv run python scripts/backfill_extraction_from_queue.py            # dry-run
    ~/.local/bin/uv run python scripts/backfill_extraction_from_queue.py --apply    # extracts
    #   add `--limit 1` to the --apply run for a one-row smoke test

Default is DRY-RUN: prints how many fetched rows lack both extract docs, writes
nothing. `--apply` costs two LLM calls per target row (claims + entities; the
entities call reuses the article prompt-cache the claims call primes). Fail-soft
per row. Re-runnable: `extraction_calls` is INSERT-not-UPSERT and the readers are
latest-wins, so a second pass just refreshes.
"""

import argparse
import hashlib
import sqlite3
from datetime import date
from pathlib import Path

from domains.queue_store.sources import (
    get_all_candidates,
    get_all_claims,
    get_row,
    record_candidates,
    record_claims,
)
from domains.types import IngestItem
from domains.wiki.claims import parse_claims_doc, render_claims
from workflows.wiki_synthesis.extract_claims import extract_claims as run_extract_claims
from workflows.wiki_synthesis.extract_entities import extract_entities as run_extract_entities
from workflows.wiki_synthesis.extract_entities import render_candidates
from workflows.wiki_synthesis.prompts import (
    EXTRACT_ARTICLE_ENVELOPE,
    EXTRACT_CLAIMS_TASK,
    EXTRACT_ENTITIES_TASK,
    EXTRACT_SHARED_SYSTEM,
)

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE_DB = _ROOT / "data" / "queue.db"

# ponytail: mirrors the fetch_extract_queue asset's per-prompt staleness handle
# (sha256 over the static prompt parts). Recomputed here to keep this script
# dagster-free like its sibling backfill; prompt_sha256 is provenance only.
_CLAIMS_SHA = hashlib.sha256(
    (EXTRACT_SHARED_SYSTEM + EXTRACT_ARTICLE_ENVELOPE + EXTRACT_CLAIMS_TASK).encode()
).hexdigest()
_ENTITIES_SHA = hashlib.sha256(
    (EXTRACT_SHARED_SYSTEM + EXTRACT_ARTICLE_ENVELOPE + EXTRACT_ENTITIES_TASK).encode()
).hexdigest()


def _fetched_ids(queue_db: Path) -> list[str]:
    """page_ids with a fetched body — sorted."""
    with sqlite3.connect(queue_db) as conn:
        rows = conn.execute(
            "SELECT notion_page_id FROM queue_items "
            "WHERE raw_content IS NOT NULL AND raw_content != '' ORDER BY notion_page_id"
        ).fetchall()
    return [r[0] for r in rows]


def _ingest_item(row: dict) -> IngestItem:
    """Same mapping the extract_claims asset uses (`_ingest_item_from_row`):
    item_id is canonical_url (fallback url), body is raw_content."""
    content_date = row.get("content_date")
    return IngestItem(
        item_id=row.get("canonical_url") or row["url"],
        title=row.get("title") or "",
        date=date.fromisoformat(content_date) if content_date else None,
        text=row.get("raw_content") or "",
        source_type="queue",
        source_ref=row["notion_page_id"],
        author=row.get("author"),
    )


def _extract_one(queue_db: Path, pid: str) -> tuple[int, int]:
    """Run claims then entities for one page, record both. Returns (n_claims,
    n_candidates). Runs both in-process so the entities call reuses the article
    prompt-cache the claims call primes — matching the asset's shared prefix."""
    row = get_row(db_path=queue_db, notion_page_id=pid)
    item = _ingest_item(row)
    content_shape = row.get("content_shape") or "unknown"

    claim_set, claim_call = run_extract_claims(item, content_shape=content_shape)
    claims_doc = render_claims(claim_set)
    record_claims(
        db_path=queue_db,
        notion_page_id=pid,
        output=claims_doc,
        prompt_label="extract_claims_v1",
        prompt_sha256=_CLAIMS_SHA,
        model=claim_call.model,
        tokens_in=claim_call.input_tokens,
        tokens_out=claim_call.output_tokens,
    )

    candidates, ent_call = run_extract_entities(item, parse_claims_doc(claims_doc))
    record_candidates(
        db_path=queue_db,
        notion_page_id=pid,
        output=render_candidates(candidates),
        prompt_label="extract_entities_v1",
        prompt_sha256=_ENTITIES_SHA,
        model=ent_call.model,
        tokens_in=ent_call.input_tokens,
        tokens_out=ent_call.output_tokens,
        cached_tokens=ent_call.cached_tokens,
    )
    return len(claim_set.claims), len(candidates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-db", type=Path, default=DEFAULT_QUEUE_DB)
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry-run.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap targets processed (smoke test)."
    )
    args = parser.parse_args()

    claims = dict(get_all_claims(db_path=args.queue_db))
    candidates = dict(get_all_candidates(db_path=args.queue_db))
    complete = claims.keys() & candidates.keys()  # pages that already have BOTH docs
    targets = [pid for pid in _fetched_ids(args.queue_db) if pid not in complete]
    if args.limit is not None:
        targets = targets[: args.limit]

    print(f"{len(targets)} fetched row(s) need extraction ({len(complete)} already complete).")
    if not args.apply:
        print("DRY-RUN — re-run with --apply to extract.")
        return

    ok = failed = 0
    for i, pid in enumerate(targets, 1):
        try:
            n_claims, n_cand = _extract_one(args.queue_db, pid)
            ok += 1
            print(f"  [{i}/{len(targets)}] {pid}: {n_claims} claims, {n_cand} candidates")
        except Exception as exc:  # fail-soft: one bad row shouldn't abort the batch
            failed += 1
            print(f"  [{i}/{len(targets)}] {pid}: FAILED — {exc!r}")
    print(f"Done: {ok} extracted, {failed} failed.")


if __name__ == "__main__":
    main()
