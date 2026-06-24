"""``wiki-merge`` console script — the curated entity-dedup primitive (#15).

Folds one entity (``--drop``) into another (``--keep``) across both wiki.db and
the on-disk ``.md`` files. The DB side is the atomic ``state.merge_entities``
transaction; this module adds the file-system side (delete drop's page, re-render
keep's frontmatter from the now-unioned ledgers) and the operator interface.

Runs in-cluster (where wiki.db AND the wiki/ files live):

    uv run wiki-merge --db data/wiki.db --wiki-dir data/wiki \\
      --keep e_d624554e26144b70 --drop e_34db8db7ab234f66

``--no-alias`` is the homonym escape hatch — skip writing drop's name as an
alias of keep so a future different-sense mention mints fresh (safe false-split).
``--dry-run`` reports the plan and rolls the transaction back, touching nothing.

NEVER run against prod during the synthesis window — SQLite is single-writer and
a concurrent merge can corrupt a read-resolve-write tick.

Atomicity is DB-only: the txn commits before the file ops. A crash in between
orphans drop's .md (harmless — synthesis reads the DB; `rm` it if you like).
"""

import argparse
import sys
from pathlib import Path

from domains.wiki.io import read_page, write_page
from domains.wiki.state import (
    MergeResult,
    connection,
    count_sources_for_entity,
    get_aliases_for_entity,
    get_page,
    get_related_for_entity,
    get_source_ids_for_entity,
    merge_entities,
)


def _rerender_keep(conn, wiki_dir: Path, keep_id: str) -> None:
    """Re-render keep's frontmatter from the now-unioned ledgers (mirrors
    synthesize._write_pages). Prose (body/summary) is left as-is — it lags until
    a new source triggers re-synthesis; only the producer-authoritative
    frontmatter (aliases / num_sources / sources / related) refreshes."""
    page_rec = get_page(conn, keep_id)
    if page_rec is None:
        return
    path = wiki_dir / page_rec.file_path
    write_page(
        path,
        read_page(path),
        aliases=get_aliases_for_entity(conn, keep_id),
        num_sources=count_sources_for_entity(conn, keep_id),
        sources=get_source_ids_for_entity(conn, keep_id),
        related=get_related_for_entity(conn, keep_id),
    )


def run_merge(
    db_path: Path | str,
    wiki_dir: Path | str,
    *,
    keep_id: str,
    drop_id: str,
    alias: bool = True,
    dry_run: bool = False,
) -> MergeResult:
    """Fold drop into keep across wiki.db and the wiki/ files. The DB
    transaction commits first; file ops (unlink drop's .md, re-render keep)
    follow. ``dry_run`` rolls back and skips the file ops."""
    wiki_dir = Path(wiki_dir)
    with connection(db_path) as conn:
        # Refuse a page-less keep BEFORE the destructive merge — almost always a
        # wrong --keep id; failing here leaves drop intact for a corrected retry.
        if get_page(conn, keep_id) is None:
            raise ValueError(
                f"keep entity {keep_id} has no page to merge into "
                "(wrong --keep id, or synthesize it first)"
            )
        result = merge_entities(conn, keep_id=keep_id, drop_id=drop_id, alias=alias)
        if dry_run:
            conn.rollback()
            return result
        conn.commit()

        if result.drop_file_path:
            (wiki_dir / result.drop_file_path).unlink(missing_ok=True)
        _rerender_keep(conn, wiki_dir, keep_id)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_merge(
        args.db,
        args.wiki_dir,
        keep_id=args.keep,
        drop_id=args.drop,
        alias=not args.no_alias,
        dry_run=args.dry_run,
    )
    verb = "would merge" if args.dry_run else "merged"
    alias_note = "" if not args.no_alias else " (no alias written)"
    print(
        f"{verb} {result.drop_id} → {result.keep_id}{alias_note}; "
        f"drop file: {result.drop_file_path or '(none)'}",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-merge", description="Fold one wiki entity into another."
    )
    parser.add_argument("--db", type=Path, required=True, help="path to wiki.db")
    parser.add_argument("--wiki-dir", type=Path, required=True, help="dir holding the .md pages")
    parser.add_argument("--keep", required=True, help="entity_id of the survivor")
    parser.add_argument("--drop", required=True, help="entity_id to fold in and delete")
    parser.add_argument(
        "--no-alias",
        action="store_true",
        help="do NOT alias drop's name onto keep (homonym escape hatch)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan and roll back; change nothing"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
