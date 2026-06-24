"""``wiki-reject`` console script — delete an auto-discovered entity the curator
judges to be noise (site chrome, mis-extraction), and tombstone it (#15).

The DB side is the atomic ``state.reject_entity`` transaction (tombstones the
canonical name + every alias into ``rejected_entities``, then deletes the
entity + cascades); this module adds the file op (unlink the ``.md``) and the
operator interface. Rejection deletes the page (rejected entities must leave
recall / vectors / toc); the name-keyed ``rejected_entities`` table is the
durable audit + undo.

Runs in-cluster (where wiki.db AND the wiki/ files live):

    uv run wiki-reject --db data/wiki.db --wiki-dir data/wiki --entity e_<id>
    uv run wiki-reject --db data/wiki.db --wiki-dir data/wiki --name "Cookie Policy"

``--dry-run`` reports the plan and rolls back. NEVER run against prod during the
synthesis window — SQLite is single-writer.

Atomicity is DB-only: the txn commits BEFORE the `.md` unlink. A crash between
leaves an orphaned `.md` (harmless — the entity is gone from the DB; clear it
with `rm` if it bothers you).
"""

import argparse
import sys
from pathlib import Path

from domains.wiki.identity import normalize_name
from domains.wiki.state import RejectResult, connection, reject_entity


def run_reject(
    db_path: Path | str,
    wiki_dir: Path | str,
    *,
    entity_id: str | None = None,
    name: str | None = None,
    category: str | None = None,
    reason: str | None = None,
    dry_run: bool = False,
) -> RejectResult:
    """Reject (delete) an entity across wiki.db and the wiki/ files. Resolve by
    ``entity_id`` or ``name``. The DB transaction commits first; the `.md` unlink
    follows. ``dry_run`` rolls back and skips the file op."""
    if not (entity_id or name):
        raise ValueError("pass entity_id or name")
    wiki_dir = Path(wiki_dir)
    with connection(db_path) as conn:
        if entity_id is None:
            row = conn.execute(
                "SELECT entity_id FROM entities WHERE normalized_name = ?",
                (normalize_name(name),),
            ).fetchone()
            if row is None:
                raise ValueError(f"no entity named {name!r}")
            entity_id = row[0]

        result = reject_entity(conn, entity_id=entity_id, category=category, reason=reason)
        if dry_run:
            conn.rollback()
            return result
        conn.commit()

        if result.file_path:
            (wiki_dir / result.file_path).unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_reject(
        args.db,
        args.wiki_dir,
        entity_id=args.entity,
        name=args.name,
        category=args.category,
        reason=args.reason,
        dry_run=args.dry_run,
    )
    verb = "would reject" if args.dry_run else "rejected"
    print(
        f"{verb} {result.entity_id}; tombstoned {result.rejected_names}; "
        f"file: {result.file_path or '(none)'}",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wiki-reject", description="Delete + tombstone an auto-discovered wiki entity."
    )
    parser.add_argument("--db", type=Path, required=True, help="path to wiki.db")
    parser.add_argument("--wiki-dir", type=Path, required=True, help="dir holding the .md pages")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--entity", help="entity_id to reject")
    target.add_argument("--name", help="canonical name to reject (resolved to its entity_id)")
    parser.add_argument("--category", help="reject category (stored on the tombstone)")
    parser.add_argument("--reason", help="reject reason (stored on the tombstone)")
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan and roll back; change nothing"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
