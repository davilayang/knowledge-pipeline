"""``wiki-merge`` console script — fold a duplicate entity ("drop") into a
survivor ("keep"), the curated dedup counter-move to the resolver's never-auto-merge
policy (#15).

The DB side is the atomic ``state.merge_entities`` transaction (re-points
``claim_entities`` + ``aliases`` drop→keep, aliases drop's name onto keep, deletes
drop, bumps keep's page); this module adds the file op (unlink drop's ``.md``) and
the operator interface. ``--backup`` snapshots ``wiki.db`` first (destructive
deletes). Re-rendering the survivor is a SEPARATE post-batch ``render_pages``
sweep — NOT done here, because a merge touches no source watermark, so the
scheduled incremental sweep won't redraw keep on its own.

Runs in-cluster (where wiki.db AND the wiki/ files live):

    uv run wiki-merge --db data/wiki.db --wiki-dir data/wiki \\
        --keep e_<survivor> --drop e_<dup> [--no-alias] [--backup]

``--dry-run`` reports the plan and rolls back. ``--no-alias`` keeps two homonyms
separate (drop's name mints fresh next time). NEVER run against prod during the
synthesis window — SQLite is single-writer.

Atomicity is DB-only: the txn commits BEFORE the `.md` unlink. A crash between
leaves an orphaned `.md` (harmless — the entity is gone from the DB; `rm` it).
"""

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from domains.wiki.state import MergeResult, connection, merge_entities


def _snapshot(db_path: Path) -> Path:
    """Copy wiki.db to a timestamped ``.pre-merge-<ts>`` sibling via SQLite's
    online-backup API. NOT a raw file copy: wiki.db is WAL-mode, so a plain
    `cp`/`shutil.copy2` can miss committed rows still in `wiki.db-wal`. The backup
    API captures a consistent snapshot including the WAL. Returns the copy's path."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = db_path.with_name(f"{db_path.name}.pre-merge-{stamp}")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def run_merge(
    db_path: Path | str,
    wiki_dir: Path | str,
    *,
    keep_id: str,
    drop_id: str,
    no_alias: bool = False,
    dry_run: bool = False,
    backup: bool = False,
) -> MergeResult:
    """Merge ``drop_id`` into ``keep_id`` across wiki.db and the wiki/ files. The
    DB transaction commits first; drop's `.md` unlink follows. ``dry_run`` rolls
    back and skips the file op. ``backup`` copies wiki.db to a timestamped
    ``.pre-merge-<ts>`` sibling before mutating (skipped on dry_run)."""
    db_path = Path(db_path)
    wiki_dir = Path(wiki_dir)
    if backup and not dry_run:
        _snapshot(db_path)

    with connection(db_path) as conn:
        result = merge_entities(conn, keep_id=keep_id, drop_id=drop_id, alias=not no_alias)
        if dry_run:
            conn.rollback()
            return result
        conn.commit()

        if result.drop_file_path:
            (wiki_dir / result.drop_file_path).unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_merge(
        args.db,
        args.wiki_dir,
        keep_id=args.keep,
        drop_id=args.drop,
        no_alias=args.no_alias,
        dry_run=args.dry_run,
        backup=args.backup,
    )
    verb = "would merge" if args.dry_run else "merged"
    print(
        f"{verb} {result.drop_id} → {result.keep_id}; "
        f"unlinked: {result.drop_file_path or '(none)'}. "
        f"Run a render_pages sweep to refresh {result.keep_id}.",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-merge", description="Fold a duplicate wiki entity into a survivor."
    )
    parser.add_argument("--db", type=Path, required=True, help="path to wiki.db")
    parser.add_argument("--wiki-dir", type=Path, required=True, help="dir holding the .md pages")
    parser.add_argument("--keep", required=True, help="entity_id of the survivor")
    parser.add_argument("--drop", required=True, help="entity_id of the duplicate to fold in")
    parser.add_argument(
        "--no-alias",
        action="store_true",
        help="don't alias drop's name onto keep (homonym escape hatch)",
    )
    parser.add_argument(
        "--backup", action="store_true", help="snapshot wiki.db to .pre-merge-<ts> before mutating"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan and roll back; change nothing"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
