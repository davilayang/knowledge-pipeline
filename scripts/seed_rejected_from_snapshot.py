"""One-shot seed: curator-snapshot rejections → prod `wiki.db` `rejected_entities`.

Bootstraps the local denylist from a Notion "Wiki Pages" curator snapshot so the
curator's rejections take effect in synthesis WITHOUT depending on the entities
still existing as rows in Notion. The PULL asset resolves a Notion rejection to a
local entity by name; this script does the same name-keyed write directly from a
captured snapshot — needed because we wiped the Notion rejections during the e2e,
so a live PULL would now read zero. The names in the snapshot belong to a prior
wiki generation, so they may match no current entity — that's fine: the denylist
is name-keyed and suppresses those names on FUTURE synthesis whether or not an
entity exists today (same "ensure the row is present even if the entity is gone"
semantics as `pull_wiki_rejections`).

Runs against `data/wiki.db` on the Hetzner host (where prod synthesis reads the
denylist):

    ssh hcloud
    cd /home/deploy/knowledge-pipeline
    uv run python scripts/seed_rejected_from_snapshot.py --snapshot <curator.json>           # dry-run, prints the list
    uv run python scripts/seed_rejected_from_snapshot.py --snapshot <curator.json> --apply    # writes

Default is DRY-RUN: it prints exactly what would be upserted (name → category /
reason) and writes nothing, so you can review the list before committing. Pass
`--apply` to perform the upserts. Idempotent — `upsert_rejected` is keyed on
`normalized_name`, so re-running overwrites the same rows in place.

Only rows with `rejected == true` are seeded; other curator-annotated rows
(category/reason set but not rejected) are skipped.
"""

import argparse
import json
import sys
from pathlib import Path

DEFAULT_WIKI_DB = Path(__file__).resolve().parents[1] / "data" / "wiki.db"


def _load_rejections(snapshot_path: Path) -> list[dict]:
    """Read the curator snapshot → the rejected rows, normalized for seeding."""
    from domains.wiki.identity import normalize_name

    snap = json.loads(snapshot_path.read_text())
    rows = snap.get("rows", [])
    out: list[dict] = []
    for row in rows:
        if not row.get("rejected"):
            continue
        title = (row.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "normalized_name": normalize_name(title),
                "category": row.get("reject_category"),
                "reason": row.get("reject_reason"),
            }
        )
    out.sort(key=lambda r: r["normalized_name"])
    return out


def _print_plan(rejections: list[dict], *, captured_at: str | None) -> None:
    print(f"Snapshot captured_at: {captured_at}")
    print(f"Rejected rows to seed: {len(rejections)}\n")
    width = max((len(r["normalized_name"]) for r in rejections), default=0)
    for r in rejections:
        cat = r["category"] or "—"
        reason = f"  reason={r['reason']!r}" if r["reason"] else ""
        print(f"  {r['normalized_name']:<{width}}  [{cat}]{reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, required=True, help="curator snapshot JSON (wiki_pages_curator_*.json)"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_WIKI_DB, help="wiki.db path")
    parser.add_argument(
        "--apply", action="store_true", help="perform the upserts (default: dry-run, prints only)"
    )
    args = parser.parse_args(argv)

    if not args.snapshot.exists():
        print(f"snapshot not found at {args.snapshot}", file=sys.stderr)
        return 1
    if not args.db.exists():
        print(f"wiki.db not found at {args.db}", file=sys.stderr)
        return 1

    rejections = _load_rejections(args.snapshot)
    captured_at = json.loads(args.snapshot.read_text()).get("captured_at")
    _print_plan(rejections, captured_at=captured_at)

    if not rejections:
        print("\nNothing to seed.")
        return 0

    if not args.apply:
        print("\nDRY-RUN — nothing written. Re-run with --apply to seed.")
        return 0

    # Import here so the script doesn't pull dagster/orchestrators on the host.
    from domains.wiki.state import connection, create_schema, get_rejected, upsert_rejected

    # Ensure the schema (idempotent — CREATE TABLE IF NOT EXISTS) the same way
    # WikiResource.get_db_path does, so this runs against a wiki.db that predates
    # the rejected_entities table (e.g. a prod DB built before the denylist landed).
    create_schema(db_path=args.db)

    with connection(args.db) as conn:
        with conn:  # one atomic transaction
            for r in rejections:
                upsert_rejected(
                    conn,
                    normalized_name=r["normalized_name"],
                    category=r["category"],
                    reason=r["reason"],
                )
        total = len(get_rejected(conn))
    print(f"\nSeeded {len(rejections)} rejections. rejected_entities now has {total} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
